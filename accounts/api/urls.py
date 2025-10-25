from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("token/refresh/", views.token_refresh, name="token_refresh"),
    path("password_reset/", views.password_reset, name="password_reset"),
    path("password_confirm/<str:uidb64>/<str:token>/",
         views.password_confirm, name="password_confirm"),
    path("auth/activate/", views.ActivateAccountView.as_view(), name="activate"),
]
