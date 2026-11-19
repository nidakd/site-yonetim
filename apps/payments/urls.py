from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("tahsilat/", views.payment_list, name="payment_list"),
    path("tahsilat/yeni/", views.payment_create, name="payment_create"),
    path("tahsilat/<int:payment_id>/", views.payment_detail, name="payment_detail"),
    path("tahsilat/<int:payment_id>/makbuz/", views.receipt, name="receipt"),
    path("tahsilat/<int:payment_id>/iptal/", views.payment_cancel, name="payment_cancel"),
]
