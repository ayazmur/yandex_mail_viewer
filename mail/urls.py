"""URL-ы приложения почты."""

from django.urls import path

from . import views

app_name = 'mail'

urlpatterns = [
    path('', views.index_view, name='index'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('compose/', views.compose_view, name='compose'),
    path('message/<str:uid>/', views.message_view, name='message'),
    path('raw/<str:uid>/', views.raw_message_view, name='raw'),
]
