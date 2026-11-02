#!/usr/bin/env python
"""Удобный запуск клиента почты: `python start.py` (Codespaces и локально)."""

import importlib.util
import os
import subprocess
import sys

HOST = '0.0.0.0'
PORT = os.environ.get('PORT', '8000')


def _ensure_dependencies() -> None:
    if importlib.util.find_spec('django') is not None:
        return
    print('Django не найден — устанавливаю зависимости из requirements.txt ...')
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'])


def _print_addresses() -> None:
    codespace = os.environ.get('CODESPACE_NAME')
    domain = os.environ.get('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN')
    if codespace and domain:
        print(f'Codespaces: https://{codespace}-{PORT}.{domain}')
        print('Порт 8000 → Visibility: Private, иначе ссылка = доступ к почте.')
    print(f'Локально:   http://127.0.0.1:{PORT}')
    print('Пароль: id.yandex.ru → Безопасность → Пароли приложений («Почта»).')


def main() -> int:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mailviewer.settings')
    _ensure_dependencies()
    _print_addresses()

    from django.core.management import execute_from_command_line

    try:
        execute_from_command_line(['manage.py', 'runserver', f'{HOST}:{PORT}'])
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
