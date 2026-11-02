"""URL configuration for yandex_mail_viewer project."""

from django.urls import include, path

urlpatterns = [
    path('', include('mail.urls')),
]
