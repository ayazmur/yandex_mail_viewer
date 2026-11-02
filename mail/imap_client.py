"""Тонкая обёртка над imaplib для почты Яндекса.

Только чтение: вход, папки, заголовки писем, тело письма.
"""

from __future__ import annotations

import base64
import email
import imaplib
import re
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any

IMAP_HOST = 'imap.yandex.ru'
IMAP_PORT = 993

_UTF7_BLOCK_RE = re.compile(r'&([^&-]*)-')
_FOLDER_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.*)$')
_UID_RE = re.compile(rb'UID\s+(\d+)')

HEADER_FIELDS = '(FROM TO SUBJECT DATE)'
HEADER_QUERY = f'(UID BODY.PEEK[HEADER.FIELDS {HEADER_FIELDS}])'


class MailError(Exception):
    """Ошибка работы с почтовым сервером.

    kind: 'auth' — сервер не принял логин/пароль, 'network' — сеть,
    'other' — всё остальное.
    """

    def __init__(self, message: str, kind: str = 'other') -> None:
        super().__init__(message)
        self.kind = kind


def decode_utf7(value: str) -> str:
    """Декодирует имя папки из modified UTF-7 (RFC 3501) в юникод."""

    def _decode_block(match: re.Match[str]) -> str:
        encoded = match.group(1)
        if not encoded:
            return '&'
        data = encoded.replace(',', '/')
        padding = '=' * (-len(data) % 4)
        raw = base64.b64decode(data + padding)
        return raw.decode('utf-16-be', errors='replace')

    return _UTF7_BLOCK_RE.sub(_decode_block, value)


def _quote_mailbox(name: str) -> str:
    return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'


def connect(email_address: str, password: str) -> imaplib.IMAP4_SSL:
    """Открывает IMAP-соединение и логинится. Бросает MailError."""
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
    except OSError as exc:
        raise MailError('Не удалось подключиться к imap.yandex.ru.', 'network') from exc
    try:
        conn.login(email_address, password)
    except imaplib.IMAP4.error as exc:
        raise MailError(
            'Яндекс не принял логин или пароль. Нужен пароль приложения для «Почты» '
            '(id.yandex.ru → Безопасность → Пароли приложений).',
            'auth',
        ) from exc
    except OSError as exc:
        raise MailError('Не удалось подключиться к imap.yandex.ru.', 'network') from exc
    return conn


def close_quietly(conn: imaplib.IMAP4_SSL | None) -> None:
    if conn is None:
        return
    try:
        conn.logout()
    except Exception:
        pass


def list_folders(conn: imaplib.IMAP4_SSL) -> list[dict[str, str]]:
    """Возвращает папки: name — как на сервере (для запросов), title — для показа."""
    try:
        status, data = conn.list()
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError('Не удалось получить список папок.') from exc
    if status != 'OK':
        raise MailError('Не удалось получить список папок.')

    folders: list[dict[str, str]] = []
    for raw in data or []:
        if not isinstance(raw, bytes):
            continue
        line = raw.decode('utf-8', errors='replace')
        match = _FOLDER_RE.match(line)
        if not match:
            continue
        raw_name = match.group('name').strip()
        if raw_name.startswith('"') and raw_name.endswith('"'):
            raw_name = raw_name[1:-1]
        title = decode_utf7(raw_name)
        if title.upper() == '[GMAIL]' or '\\noselect' in match.group('flags').lower():
            continue
        folders.append({'name': raw_name, 'title': title})

    folders.sort(key=lambda item: (item['name'].upper() != 'INBOX', item['title'].lower()))
    return folders


def select_folder(conn: imaplib.IMAP4_SSL, folder: str) -> None:
    try:
        status, _ = conn.select(_quote_mailbox(folder), readonly=True)
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError(f'Не удалось открыть папку «{decode_utf7(folder)}».') from exc
    if status != 'OK':
        raise MailError(f'Не удалось открыть папку «{decode_utf7(folder)}».')


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ''
    try:
        return str(make_header(decode_header(value)))
    except (email.errors.HeaderParseError, UnicodeDecodeError):
        return value


def _format_date(value: str | None) -> str:
    if not value:
        return ''
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value
    if parsed is None:
        return value
    try:
        return parsed.astimezone().strftime('%d.%m.%Y %H:%M')
    except (OSError, OverflowError, ValueError):
        return value


def fetch_headers_page(
    conn: imaplib.IMAP4_SSL, page: int, page_size: int
) -> tuple[list[dict[str, str]], int]:
    """Заголовки одной страницы писем, новые сверху. Возвращает (письма, всего)."""
    try:
        status, data = conn.uid('search', None, 'ALL')
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError('Не удалось получить список писем.') from exc
    if status != 'OK':
        raise MailError('Не удалось получить список писем.')

    raw_uids = data[0] if data else b''
    all_uids = raw_uids.split() if isinstance(raw_uids, bytes) else []
    total = len(all_uids)

    newest_first = list(reversed(all_uids))
    start = max(0, (page - 1) * page_size)
    page_uids = [uid.decode('ascii', errors='ignore') for uid in newest_first[start:start + page_size]]
    page_uids = [uid for uid in page_uids if uid]
    if not page_uids:
        return [], total

    try:
        status, parts = conn.uid('fetch', ','.join(page_uids), HEADER_QUERY)
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError('Не удалось загрузить заголовки писем.') from exc
    if status != 'OK':
        raise MailError('Не удалось загрузить заголовки писем.')

    headers_by_uid: dict[str, bytes] = {}
    for part in parts or []:
        if not (isinstance(part, tuple) and len(part) >= 2):
            continue
        meta, payload = part[0], part[1]
        if not isinstance(payload, bytes):
            continue
        meta_bytes = meta if isinstance(meta, bytes) else str(meta).encode('utf-8', 'replace')
        match = _UID_RE.search(meta_bytes)
        if not match:
            continue
        headers_by_uid[match.group(1).decode('ascii', errors='ignore')] = payload

    messages: list[dict[str, str]] = []
    for uid in page_uids:
        payload = headers_by_uid.get(uid)
        if payload is None:
            continue
        msg = email.message_from_bytes(payload)
        messages.append({
            'uid': uid,
            'from': _decode_header_value(msg.get('From')),
            'to': _decode_header_value(msg.get('To')),
            'subject': _decode_header_value(msg.get('Subject')) or '(без темы)',
            'date': _format_date(msg.get('Date')),
        })
    return messages, total


def _extract_bodies(msg: Message) -> tuple[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = (part.get('Content-Disposition') or '').lower()
        if 'attachment' in disposition:
            continue
        content_type = part.get_content_type()
        if content_type not in ('text/plain', 'text/html'):
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            continue
        charset = part.get_content_charset() or 'utf-8'
        try:
            decoded = payload.decode(charset, errors='replace')
        except LookupError:
            decoded = payload.decode('utf-8', errors='replace')
        if content_type == 'text/plain':
            text_parts.append(decoded)
        else:
            html_parts.append(decoded)
    return '\n\n'.join(text_parts).strip(), '\n'.join(html_parts).strip()


def fetch_message(conn: imaplib.IMAP4_SSL, uid: str) -> dict[str, Any]:
    try:
        status, parts = conn.uid('fetch', uid, '(BODY.PEEK[])')
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError('Не удалось прочитать письмо.') from exc
    if status != 'OK':
        raise MailError('Не удалось прочитать письмо.')

    raw = b''
    for part in parts or []:
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
            raw = part[1]
            break
    if not raw:
        raise MailError('Письмо не найдено или пустое.')

    msg = email.message_from_bytes(raw)
    text, html = _extract_bodies(msg)
    return {
        'uid': uid,
        'from': _decode_header_value(msg.get('From')),
        'to': _decode_header_value(msg.get('To')),
        'cc': _decode_header_value(msg.get('Cc')),
        'subject': _decode_header_value(msg.get('Subject')) or '(без темы)',
        'date': _format_date(msg.get('Date')),
        'text': text,
        'html': html,
    }
