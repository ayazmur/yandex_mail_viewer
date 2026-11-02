"""Тонкая обёртка над imaplib для почты Яндекса."""

from __future__ import annotations

import base64
import email
import imaplib
import logging
import re
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Iterator

IMAP_HOST = 'imap.yandex.ru'
IMAP_PORT = 993
FETCH_BATCH_SIZE = 200

logger = logging.getLogger(__name__)

_UTF7_BLOCK_RE = re.compile(r'&([^&-]*)-')
_FOLDER_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.*)$')
_UID_RE = re.compile(rb'UID\s+(\d+)')
_FLAGS_RE = re.compile(rb'FLAGS \(([^)]*)\)')

HEADER_FIELDS = '(FROM TO SUBJECT DATE)'
HEADER_QUERY = f'(UID FLAGS BODY.PEEK[HEADER.FIELDS {HEADER_FIELDS}])'


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


def normalize_account(value: str) -> str:
    """Приводит адрес к виду login@yandex.ru, если домен не указан."""
    value = (value or '').strip()
    if value and '@' not in value:
        value += '@yandex.ru'
    return value


def _server_reason(exc: Exception) -> str:
    text = str(exc).strip()
    if len(text) >= 3 and text[0] == 'b' and text[1] in ("'", '"'):
        text = text[2:-1]
    text = text.replace('\\r', ' ').replace('\\n', ' ')
    return text[:300]


def _open_connection() -> imaplib.IMAP4_SSL:
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            return imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
        except (OSError, imaplib.IMAP4.error) as exc:
            last_error = exc
    logger.warning(f'IMAP: соединение с {IMAP_HOST} не установлено: {last_error}')
    raise MailError(
        'Не удалось связаться с imap.yandex.ru: соединение закрывается. '
        'Попробуйте ещё раз через минуту.',
        'network',
    ) from last_error


def _connect_once(email_address: str, password: str) -> imaplib.IMAP4_SSL:
    conn = _open_connection()
    try:
        conn.login(email_address, password)
    except imaplib.IMAP4.abort as exc:
        close_quietly(conn)
        logger.warning(f'IMAP: сервер оборвал соединение при входе: {exc}')
        raise MailError('Соединение с imap.yandex.ru оборвалось. Попробуйте ещё раз.', 'network') from exc
    except imaplib.IMAP4.error as exc:
        close_quietly(conn)
        reason = _server_reason(exc)
        logger.warning(f'IMAP: Яндекс отклонил вход для {email_address}: {reason}')
        detail = f' Ответ сервера: {reason}.' if reason else ''
        raise MailError(
            'Яндекс не принял логин или пароль. Проверьте: пароль приложения типа '
            '«Почта» (без пробелов), в настройках Почты включён доступ по IMAP '
            '(«Почтовые программы»), логин — полный адрес ящика.' + detail,
            'auth',
        ) from exc
    except OSError as exc:
        close_quietly(conn)
        logger.warning(f'IMAP: обрыв соединения при входе: {exc}')
        raise MailError('Соединение с imap.yandex.ru оборвалось. Попробуйте ещё раз.', 'network') from exc
    return conn


def connect(email_address: str, password: str) -> imaplib.IMAP4_SSL:
    """Открывает IMAP-соединение и логинится. Бросает MailError."""
    email_address = normalize_account(email_address)
    password = (password or '').strip()

    candidates = [password]
    # Пароли приложений Яндекса показываются группами; при копировании в них
    # попадают пробелы, поэтому при отказе пробуем ещё раз без них.
    without_spaces = ''.join(password.split())
    if without_spaces != password:
        candidates.append(without_spaces)

    last_error: MailError | None = None
    for candidate in candidates:
        try:
            return _connect_once(email_address, candidate)
        except MailError as exc:
            last_error = exc
            if exc.kind != 'auth':
                raise
    raise last_error


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


def _date_ts(value: str | None) -> int:
    if not value:
        return 0
    try:
        parsed = parsedate_to_datetime(value)
        if parsed is None:
            return 0
        return int(parsed.timestamp())
    except (TypeError, ValueError, OSError, OverflowError):
        return 0


def _address(value: str | None) -> tuple[str, str]:
    name, address = parseaddr(value or '')
    return _decode_header_value(name).strip(), (address or '').strip()


def _message_summary(uid: str, msg: Message, flags: bytes) -> dict[str, Any]:
    name, address = _address(msg.get('From'))
    return {
        'uid': uid,
        'from': _decode_header_value(msg.get('From')),
        'from_name': name,
        'from_addr': address,
        'to': _decode_header_value(msg.get('To')),
        'subject': _decode_header_value(msg.get('Subject')),
        'date': _format_date(msg.get('Date')),
        'date_ts': _date_ts(msg.get('Date')),
        'unread': b'\\Seen' not in (flags or b''),
    }


def _fetch_headers_batch(
    conn: imaplib.IMAP4_SSL, uids: list[str]
) -> dict[str, tuple[bytes, bytes]]:
    if not uids:
        return {}
    try:
        status, parts = conn.uid('fetch', ','.join(uids), HEADER_QUERY)
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError('Не удалось загрузить заголовки писем.') from exc
    if status != 'OK':
        raise MailError('Не удалось загрузить заголовки писем.')

    by_uid: dict[str, tuple[bytes, bytes]] = {}
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
        flags_match = _FLAGS_RE.search(meta_bytes)
        flags = flags_match.group(1) if flags_match else b''
        by_uid[match.group(1).decode('ascii', 'ignore')] = (payload, flags)
    return by_uid


def fetch_folder_headers(
    conn: imaplib.IMAP4_SSL, limit: int | None = None
) -> tuple[list[dict[str, Any]], int]:
    """Все письма папки (новые сверху) в пределах limit. Возвращает (письма, всего)."""
    try:
        status, data = conn.uid('search', None, 'ALL')
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError('Не удалось получить список писем.') from exc
    if status != 'OK':
        raise MailError('Не удалось получить список писем.')

    raw_uids = data[0] if data else b''
    all_uids = raw_uids.split() if isinstance(raw_uids, bytes) else []
    total = len(all_uids)

    newest_first = [
        uid.decode('ascii', 'ignore') for uid in reversed(all_uids) if uid
    ]
    newest_first = [uid for uid in newest_first if uid]
    if limit is not None:
        newest_first = newest_first[:limit]

    messages: list[dict[str, Any]] = []
    for start in range(0, len(newest_first), FETCH_BATCH_SIZE):
        batch = newest_first[start:start + FETCH_BATCH_SIZE]
        fetched = _fetch_headers_batch(conn, batch)
        for uid in batch:
            item = fetched.get(uid)
            if item is None:
                continue
            payload, flags = item
            messages.append(_message_summary(uid, email.message_from_bytes(payload), flags))
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


def _safe_filename(value: str | None) -> str:
    name = (value or '').replace('\\', '/').split('/')[-1]
    name = ''.join(ch for ch in name if ch.isprintable()).strip().strip('.')
    return (name or 'attachment')[:200]


def _iter_attachment_parts(msg: Message) -> Iterator[tuple[int, Message, str]]:
    for index, part in enumerate(msg.walk()):
        if part.is_multipart():
            continue
        disposition = (part.get('Content-Disposition') or '').lower()
        is_attachment = 'attachment' in disposition
        filename = part.get_filename()
        content_type = part.get_content_type()
        if filename:
            filename = _decode_header_value(filename)
        if not filename and not is_attachment:
            continue
        if not is_attachment and content_type in ('text/plain', 'text/html'):
            continue
        yield index, part, _safe_filename(filename)


def _part_size(part: Message) -> int:
    payload = part.get_payload(decode=True)
    return len(payload) if isinstance(payload, bytes) else 0


def _fetch_raw_message(conn: imaplib.IMAP4_SSL, uid: str) -> Message:
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
    return email.message_from_bytes(raw)


def fetch_message(conn: imaplib.IMAP4_SSL, uid: str) -> dict[str, Any]:
    msg = _fetch_raw_message(conn, uid)
    text, html = _extract_bodies(msg)
    name, address = _address(msg.get('From'))
    attachments = [
        {
            'index': index,
            'filename': filename,
            'content_type': part.get_content_type(),
            'size': _part_size(part),
        }
        for index, part, filename in _iter_attachment_parts(msg)
    ]
    return {
        'uid': uid,
        'from': _decode_header_value(msg.get('From')),
        'from_name': name,
        'from_addr': address,
        'to': _decode_header_value(msg.get('To')),
        'cc': _decode_header_value(msg.get('Cc')),
        'subject': _decode_header_value(msg.get('Subject')),
        'date': _format_date(msg.get('Date')),
        'date_ts': _date_ts(msg.get('Date')),
        'unread': False,
        'text': text,
        'html': html,
        'attachments': attachments,
    }


def fetch_attachment(
    conn: imaplib.IMAP4_SSL, uid: str, index: int
) -> tuple[str, str, bytes]:
    msg = _fetch_raw_message(conn, uid)
    for part_index, part, filename in _iter_attachment_parts(msg):
        if part_index == index:
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                payload = b''
            return filename, part.get_content_type(), payload
    raise MailError('Вложение не найдено.')
