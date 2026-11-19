from django.urls import path

from . import views

app_name = "expenses"

urlpatterns = [
    path("giderler/", views.expense_list, name="expense_list"),
    path("giderler/<int:expense_id>/iptal/", views.expense_cancel, name="expense_cancel"),
    path("kasa-banka/", views.cash_bank, name="cash_bank"),
]
