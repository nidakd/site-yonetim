from django.urls import path

from . import views

app_name = "imports"

urlpatterns = [
    path("kurulum/", views.setup_home, name="setup_home"),
    path("kurulum/sablon.xlsx", views.template_download, name="template"),
    path("kurulum/<int:job_id>/", views.job_detail, name="job_detail"),
    path("kurulum/<int:job_id>/uygula/", views.job_apply, name="job_apply"),
    path("kurulum/<int:job_id>/vazgec/", views.job_discard, name="job_discard"),
]
