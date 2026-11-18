from django.urls import path

from . import views

app_name = "tenancy"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("uyelik-yok/", views.no_organization, name="no_organization"),
    path("kurulus/<int:org_id>/sec/", views.switch_organization, name="switch_organization"),
    path("siteler/yeni/", views.site_create, name="site_create"),
    path("siteler/<int:site_id>/", views.site_detail, name="site_detail"),
    path("siteler/<int:site_id>/duzenle/", views.site_update, name="site_update"),
]
