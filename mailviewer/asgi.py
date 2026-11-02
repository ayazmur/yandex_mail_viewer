"""ASGI config for yandex_mail_viewer project."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mailviewer.settings')

application = get_asgi_application()
