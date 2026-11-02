"""Веб-клиент Яндекс.Почты: вход, список писем, чтение, отправка."""

from __future__ import annotations

import re
from functools import wraps
from typing import Callable
from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from . import imap_client, smtp_client
from .imap_client import MailError

SESSION_EMAIL = 'mail_email'
SESSION_PASSWORD = 'mail_password'
SESSION_FOLDER = 'mail_folder'
SESSION_FLASH = 'mail_flash'

DEFAULT_FOLDER = 'INBOX'
_RECIPIENTS_RE = re.compile(r'[;,\s]+')


def _credentials(request: HttpRequest) -> tuple[str | None, str | None]:
    return request.session.get(SESSION_EMAIL), request.session.get(SESSION_PASSWORD)


def _safe_next(value: str | None) -> str:
    if value and value.startswith('/') and not value.startswith('//'):
        return value
    return reverse('mail:index')


def mail_login_required(view: Callable) -> Callable:
    """Требует, чтобы в сессии были адрес и пароль приложения."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs):
        email_address, password = _credentials(request)
        if not email_address or not password:
            login_url = reverse('mail:login')
            return redirect(f'{login_url}?{urlencode({"next": request.get_full_path()})}')
        request.mail_email = email_address
        request.mail_password = password
        return view(request, *args, **kwargs)

    return wrapper


def _login_page(
    request: HttpRequest,
    error: str | None = None,
    email_address: str = '',
) -> HttpResponse:
    return render(request, 'mail/login.html', {
        'error': error,
        'email': email_address,
        'next': _safe_next(request.GET.get('next') or request.POST.get('next')),
    })


def _parse_page(value: str | None) -> int:
    try:
        return max(1, int(value or '1'))
    except (TypeError, ValueError):
        return 1


def _folder_title(folders: list[dict[str, str]], folder: str) -> str:
    for item in folders:
        if item['name'] == folder:
            return item['title']
    return imap_client.decode_utf7(folder)


def _auth_expired(request: HttpRequest) -> HttpResponseRedirect:
    request.session.flush()
    return redirect('mail:login')


@require_http_methods(['GET', 'POST'])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.method == 'GET':
        if _credentials(request)[0]:
            return redirect(_safe_next(request.GET.get('next')))
        return _login_page(request)

    email_address = imap_client.normalize_account(request.POST.get('email') or '')
    password = (request.POST.get('password') or '').strip()
    if not email_address or not password:
        return _login_page(request, 'Укажите адрес и пароль приложения.', email_address)

    try:
        conn = imap_client.connect(email_address, password)
    except MailError as exc:
        return _login_page(request, str(exc), email_address)
    try:
        imap_client.select_folder(conn, DEFAULT_FOLDER)
    except MailError:
        pass
    finally:
        imap_client.close_quietly(conn)

    request.session[SESSION_EMAIL] = email_address
    request.session[SESSION_PASSWORD] = password
    request.session.pop(SESSION_FOLDER, None)
    return redirect(_safe_next(request.POST.get('next')))


@require_http_methods(['POST'])
def logout_view(request: HttpRequest) -> HttpResponse:
    request.session.flush()
    return redirect('mail:login')


@require_GET
@mail_login_required
def index_view(request: HttpRequest) -> HttpResponse:
    folder = request.GET.get('folder') or request.session.get(SESSION_FOLDER) or DEFAULT_FOLDER
    page = _parse_page(request.GET.get('page'))
    page_size = settings.MESSAGE_PAGE_SIZE
    flash = request.session.pop(SESSION_FLASH, None)

    context = {
        'email': request.mail_email,
        'folder': folder,
        'folder_title': _folder_title([], folder),
        'folders': [],
        'messages': [],
        'total': 0,
        'page': page,
        'pages': 1,
        'flash': flash,
        'error': None,
    }

    try:
        conn = imap_client.connect(request.mail_email, request.mail_password)
        try:
            folders = imap_client.list_folders(conn)
            imap_client.select_folder(conn, folder)
            messages, total = imap_client.fetch_headers_page(conn, page, page_size)
        finally:
            imap_client.close_quietly(conn)
    except MailError as exc:
        if exc.kind == 'auth':
            return _auth_expired(request)
        context['error'] = str(exc)
        request.session[SESSION_FOLDER] = folder
        return render(request, 'mail/index.html', context)

    request.session[SESSION_FOLDER] = folder
    context.update({
        'folders': folders,
        'folder_title': _folder_title(folders, folder),
        'messages': messages,
        'total': total,
        'pages': max(1, (total + page_size - 1) // page_size),
    })
    return render(request, 'mail/index.html', context)


@require_GET
@mail_login_required
def message_view(request: HttpRequest, uid: str) -> HttpResponse:
    if not uid.isdigit():
        raise Http404('Письмо не найдено.')
    folder = request.GET.get('folder') or request.session.get(SESSION_FOLDER) or DEFAULT_FOLDER
    page = _parse_page(request.GET.get('page'))

    context = {
        'email': request.mail_email,
        'folder': folder,
        'folder_title': _folder_title([], folder),
        'folders': [],
        'uid': uid,
        'page': page,
        'message': None,
        'error': None,
    }

    try:
        conn = imap_client.connect(request.mail_email, request.mail_password)
        try:
            folders = imap_client.list_folders(conn)
            imap_client.select_folder(conn, folder)
            message = imap_client.fetch_message(conn, uid)
        finally:
            imap_client.close_quietly(conn)
    except MailError as exc:
        if exc.kind == 'auth':
            return _auth_expired(request)
        context['error'] = str(exc)
        return render(request, 'mail/message.html', context)

    context.update({
        'folders': folders,
        'folder_title': _folder_title(folders, folder),
        'message': message,
    })
    return render(request, 'mail/message.html', context)


@require_GET
@mail_login_required
def raw_message_view(request: HttpRequest, uid: str) -> HttpResponse:
    """HTML-тело письма для sandbox-iframe: без скриптов, без внешних ресурсов."""
    if not uid.isdigit():
        raise Http404('Письмо не найдено.')
    folder = request.GET.get('folder') or request.session.get(SESSION_FOLDER) or DEFAULT_FOLDER

    try:
        conn = imap_client.connect(request.mail_email, request.mail_password)
        try:
            imap_client.select_folder(conn, folder)
            message = imap_client.fetch_message(conn, uid)
        finally:
            imap_client.close_quietly(conn)
    except MailError as exc:
        return HttpResponse(
            f'<p style="font-family:sans-serif">{str(exc)}</p>',
            content_type='text/html; charset=utf-8',
            status=502,
        )

    body = message.get('html') or '<p>В письме нет HTML-версии.</p>'
    response = HttpResponse(body, content_type='text/html; charset=utf-8')
    response['Content-Security-Policy'] = (
        "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    )
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'no-store'
    return response


@require_http_methods(['GET', 'POST'])
@mail_login_required
def compose_view(request: HttpRequest) -> HttpResponse:
    form = {
        'to': request.GET.get('to', ''),
        'subject': '',
        'body': '',
    }
    error = None

    if request.method == 'POST':
        form['to'] = (request.POST.get('to') or '').strip()
        form['subject'] = (request.POST.get('subject') or '').strip()
        form['body'] = request.POST.get('body') or ''

        recipients = [item for item in _RECIPIENTS_RE.split(form['to']) if item]
        if not recipients or any('@' not in item for item in recipients):
            error = 'Укажите получателей через запятую — каждый адрес должен содержать «@».'
        elif not form['body'].strip():
            error = 'Текст письма пуст.'
        else:
            try:
                smtp_client.send_message(
                    request.mail_email,
                    request.mail_password,
                    recipients,
                    form['subject'] or '(без темы)',
                    form['body'],
                )
            except MailError as exc:
                if exc.kind == 'auth':
                    return _auth_expired(request)
                error = str(exc)
            else:
                request.session[SESSION_FLASH] = 'Письмо отправлено.'
                return redirect(f"{reverse('mail:index')}?folder=INBOX")

    folders: list[dict[str, str]] = []
    try:
        conn = imap_client.connect(request.mail_email, request.mail_password)
    except MailError as exc:
        if exc.kind == 'auth':
            return _auth_expired(request)
    else:
        try:
            folders = imap_client.list_folders(conn)
        except MailError:
            folders = []
        finally:
            imap_client.close_quietly(conn)

    return render(request, 'mail/compose.html', {
        'email': request.mail_email,
        'folders': folders,
        'folder': request.session.get(SESSION_FOLDER) or DEFAULT_FOLDER,
        'form': form,
        'error': error,
    })
