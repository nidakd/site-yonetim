from django.urls import path

from . import views

app_name = "property"

urlpatterns = [
    path("bolumler/", views.unit_list, name="unit_list"),
    path("bolumler/yeni/", views.unit_create, name="unit_create"),
    path("bolumler/toplu/", views.unit_bulk_create, name="unit_bulk_create"),
    path("bolumler/<int:unit_id>/", views.unit_detail, name="unit_detail"),
    path("bolumler/<int:unit_id>/duzenle/", views.unit_update, name="unit_update"),
    path("bolumler/<int:unit_id>/arsiv/", views.unit_archive, name="unit_archive"),
    path("bloklar/", views.block_list, name="block_list"),
    path("yapi/", views.structure, name="structure"),
    path("yapi/blok/<int:block_id>/", views.block_update, name="block_update"),
    path("yapi/tip/<int:type_id>/", views.unit_type_update, name="unit_type_update"),
]
