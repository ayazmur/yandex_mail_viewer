"""Группировка писем в треды по теме и оформление списка."""

from __future__ import annotations

import re
import zlib

_PREFIX_RE = re.compile(
    r'^\s*(?:(?:re|fw|fwd|aw|sv|vs|отв|ответ|пересл|переслать)\s*(?:\[\d+\])?\s*:\s*)+',
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r'\s+')

EMPTY_SUBJECT = '(без темы)'
AVATAR_COLORS = 8


def normalize_subject(subject: str) -> str:
    cleaned = _PREFIX_RE.sub('', subject or '')
    cleaned = _SPACE_RE.sub(' ', cleaned).strip()
    if cleaned.casefold() == EMPTY_SUBJECT.casefold():
        return ''
    return cleaned


def thread_key(message: dict) -> str:
    normalized = normalize_subject(message.get('subject', ''))
    if not normalized:
        return f"uid:{message['uid']}"
    return normalized.casefold()


def decorate(messages: list[dict]) -> None:
    """Добавляет к письмам инициал и цвет аватара."""
    for message in messages:
        source = (message.get('from_name') or message.get('from_addr') or '?').strip()
        initial = next((ch for ch in source if ch.isalnum()), '?')
        message['avatar'] = initial.upper()
        address = (message.get('from_addr') or '').encode('utf-8', 'ignore')
        message['color'] = zlib.crc32(address) % AVATAR_COLORS


def build_threads(messages: list[dict]) -> list[dict]:
    """Склеивает письма (уже отсортированные: новые сверху) в треды по теме."""
    threads: dict[str, dict] = {}
    for message in messages:
        key = thread_key(message)
        thread = threads.get(key)
        if thread is None:
            subject = normalize_subject(message.get('subject', '')) or EMPTY_SUBJECT
            thread = {
                'key': key,
                'subject': subject,
                'count': 0,
                'messages': [],
                'latest': message,
                'unread': 0,
            }
            threads[key] = thread
        thread['messages'].append(message)
        thread['count'] += 1
        if message.get('unread'):
            thread['unread'] += 1

    return sorted(
        threads.values(),
        key=lambda item: (item['latest'].get('date_ts', 0), int(item['latest']['uid'])),
        reverse=True,
    )
