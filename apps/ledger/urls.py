from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("yevmiye/", views.journal, name="journal"),
    path("mizan/", views.trial_balance, name="trial_balance"),
    path("hesap/<int:account_id>/", views.account_detail, name="account_detail"),
    path("ayarlar/", views.settings_view, name="settings"),
]
