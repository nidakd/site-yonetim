from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "accounts"

urlpatterns = [
    path("giris/", views.login_view, name="login"),
    path("giris/dogrulama/", views.two_factor_view, name="two_factor"),
    path("giris/yedek-kodlar/", views.backup_codes_view, name="backup_codes"),
    path("cikis/", views.logout_view, name="logout"),
    path("hesap/guvenlik/", views.security_view, name="security"),
    path(
        "sifre/sifirla/",
        auth_views.PasswordResetView.as_view(
            template_name="registration/password_reset_form.html",
            email_template_name="registration/password_reset_email.txt",
            subject_template_name="registration/password_reset_subject.txt",
            success_url=reverse_lazy("accounts:password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "sifre/sifirla/gonderildi/",
        auth_views.PasswordResetDoneView.as_view(template_name="registration/password_reset_done.html"),
        name="password_reset_done",
    ),
    path("sifre/yeni/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path(
        "sifre/yeni/tamam/",
        auth_views.PasswordResetCompleteView.as_view(template_name="registration/password_reset_complete.html"),
        name="password_reset_complete",
    ),
]
