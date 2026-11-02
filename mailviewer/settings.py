"""Django settings for yandex_mail_viewer project."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-change-me-0f4b7c1a2d9e6f8b3c5a7d1e',
)

DEBUG = os.environ.get('DJANGO_DEBUG', '1') == '1'

ALLOWED_HOSTS = ['*']

CSRF_TRUSTED_ORIGINS = [
    'https://*.app.github.dev',  # Доверять всем адресам Codespaces
    'https://localhost:8000',
    'http://localhost:8000',
    'https://127.0.0.1:8000',
    'http://127.0.0.1:8000',
]

# В Codespaces адрес уникален для каждого пространства: собираем его из окружения,
# чтобы не хардкодить имя. Переменные задаёт сам Codespaces.
_codespace_name = os.environ.get('CODESPACE_NAME')
_forwarding_domain = os.environ.get('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN')
if _codespace_name and _forwarding_domain:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_codespace_name}-8000.{_forwarding_domain}")

# Django работает за HTTPS-прокси Codespaces
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    'django.contrib.sessions',
    'django.contrib.staticfiles',
    'mail',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
]

ROOT_URLCONF = 'mailviewer.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'mailviewer.wsgi.application'

# База данных не нужна: писем не храним, только сессии.
DATABASES = {}

# Адрес и пароль приложения живут только в оперативной памяти процесса
# (кэш locmem), не пишутся в БД и не попадают в cookie.
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'mail-sessions',
    },
}
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

MESSAGE_PAGE_SIZE = 25

# Сколько последних писем папки забирать для склейки в треды.
THREAD_HEADER_LIMIT = 3000

# Вложения: ограничения приложения (Яндекс принимает письма до ~30 МБ).
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
