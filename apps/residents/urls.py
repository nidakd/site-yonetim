from django.urls import path

from . import views

app_name = "residents"

urlpatterns = [
    path("", views.home, name="home"),
    path("giris/", views.login_phone, name="login"),
    path("giris/kod/", views.login_code, name="login_code"),
    path("giris/sifre/", views.login_password, name="login_password"),
    path("cikis/", views.logout_view, name="logout"),
    path("yonetim-sec/", views.choose_org, name="choose_org"),
    path("aydinlatma/", views.consent, name="consent"),
    path("bolum/<int:unit_id>/", views.unit_detail, name="unit_detail"),
    path("makbuz/<int:payment_id>/", views.receipt, name="receipt"),
    path("site/<int:site_id>/", views.site_summary, name="site_summary"),
    path("sifre/", views.password_settings, name="password"),
]
