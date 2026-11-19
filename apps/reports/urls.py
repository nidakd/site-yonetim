from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("borclular/", views.debtors, name="debtors"),
    path("bolumler/<int:unit_id>/ekstre/", views.unit_statement, name="unit_statement"),
]
