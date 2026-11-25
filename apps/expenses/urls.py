from django.urls import path

from . import views

app_name = "expenses"

urlpatterns = [
    path("giderler/", views.expense_list, name="expense_list"),
    path("giderler/<int:expense_id>/iptal/", views.expense_cancel, name="expense_cancel"),
    path("faturalar/", views.bill_list, name="bill_list"),
    path("faturalar/<int:bill_id>/", views.bill_detail, name="bill_detail"),
    path("faturalar/<int:bill_id>/iptal/", views.bill_cancel, name="bill_cancel"),
    path("faturalar/odeme/<int:payment_id>/iptal/", views.bill_payment_cancel, name="bill_payment_cancel"),
    path("kasa-banka/", views.cash_bank, name="cash_bank"),
]
