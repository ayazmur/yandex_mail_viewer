"""WSGI config for yandex_mail_viewer project."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mailviewer.settings')

application = get_wsgi_application()
