"""Отправка писем через smtp.yandex.ru."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formatdate

from .imap_client import MailError

SMTP_HOST = 'smtp.yandex.ru'
SMTP_PORT = 465


def send_message(
    email_address: str,
    password: str,
    recipients: list[str],
    subject: str,
    body: str,
) -> None:
    """Отправляет простое текстовое письмо. Бросает MailError."""
    message = EmailMessage()
    message['From'] = email_address
    message['To'] = ', '.join(recipients)
    message['Subject'] = subject
    message['Date'] = formatdate(localtime=True)
    message.set_content(body)

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
            smtp.login(email_address, password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            'Яндекс не принял логин или пароль приложения для SMTP.',
            'auth',
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError('Не удалось отправить письмо через smtp.yandex.ru.', 'network') from exc
