from django.urls import path

from . import views

app_name = "people"

urlpatterns = [
    path("kisiler/", views.person_list, name="person_list"),
    path("kisiler/<int:person_id>/", views.person_detail, name="person_detail"),
    path("kisiler/<int:person_id>/duzenle/", views.person_update, name="person_update"),
    path("bolumler/<int:unit_id>/kisi-ekle/", views.occupancy_create, name="occupancy_create"),
    path("iliski/<int:occupancy_id>/bitir/", views.occupancy_end, name="occupancy_end"),
]
