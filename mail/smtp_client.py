"""Отправка писем через smtp.yandex.ru."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formatdate

from .imap_client import MailError

SMTP_HOST = 'smtp.yandex.ru'
SMTP_PORT = 465

logger = logging.getLogger(__name__)

Attachment = tuple[str, str, bytes]


def _clean_filename(value: str) -> str:
    name = (value or '').replace('\\', '/').split('/')[-1]
    name = ''.join(ch for ch in name if ch.isprintable()).strip()
    return (name or 'attachment')[:200]


def _send_once(email_address: str, password: str, message: EmailMessage) -> None:
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=120) as smtp:
        smtp.login(email_address, password)
        smtp.send_message(message)


def send_message(
    email_address: str,
    password: str,
    recipients: list[str],
    subject: str,
    body: str,
    attachments: list[Attachment] | None = None,
) -> None:
    """Отправляет текстовое письмо с вложениями. Бросает MailError."""
    message = EmailMessage()
    message['From'] = email_address
    message['To'] = ', '.join(recipients)
    message['Subject'] = subject
    message['Date'] = formatdate(localtime=True)
    message.set_content(body)

    for filename, content_type, payload in attachments or []:
        maintype, _, subtype = (content_type or '').partition('/')
        message.add_attachment(
            payload,
            maintype=maintype or 'application',
            subtype=subtype or 'octet-stream',
            filename=_clean_filename(filename),
        )

    candidates = [password]
    # Пароль приложения иногда копируется с пробелами между группами.
    without_spaces = ''.join(password.split())
    if without_spaces != password:
        candidates.append(without_spaces)

    auth_error: smtplib.SMTPAuthenticationError | None = None
    for candidate in candidates:
        try:
            _send_once(email_address, candidate, message)
            return
        except smtplib.SMTPAuthenticationError as exc:
            auth_error = exc
            logger.warning(f'SMTP: Яндекс отклонил вход для {email_address}: {exc}')
        except (smtplib.SMTPException, OSError) as exc:
            logger.warning(f'SMTP: ошибка отправки для {email_address}: {exc}')
            raise MailError('Не удалось отправить письмо через smtp.yandex.ru.', 'network') from exc

    raise MailError(
        'Яндекс не принял логин или пароль приложения для SMTP. '
        'Нужен пароль приложения типа «Почта» без пробелов.',
        'auth',
    ) from auth_error
