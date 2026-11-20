from django.urls import path

from . import panel_views

app_name = "residents_panel"

urlpatterns = [path("sakin-portali/", panel_views.portal_settings, name="portal_settings")]
