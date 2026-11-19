from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("tahakkuk/", views.batch_list, name="batch_list"),
    path("tahakkuk/yeni/", views.batch_create, name="batch_create"),
    path("tahakkuk/<int:batch_id>/", views.batch_detail, name="batch_detail"),
    path("tahakkuk/<int:batch_id>/onayla/", views.batch_approve, name="batch_approve"),
    path("tahakkuk/<int:batch_id>/sil/", views.batch_discard, name="batch_discard"),
    path("tahakkuk/<int:batch_id>/iptal/", views.batch_cancel, name="batch_cancel"),
    path("bolumler/<int:unit_id>/ozel-aidat/", views.agreement_create, name="agreement_create"),
    path("ozel-aidat/<int:agreement_id>/bitir/", views.agreement_end, name="agreement_end"),
    path("borc-turleri/yeni/", views.charge_type_form, name="charge_type_create"),
    path("borc-turleri/<int:type_id>/", views.charge_type_form, name="charge_type_update"),
]
