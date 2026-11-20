from django.urls import path

from . import views

app_name = "comms"

urlpatterns = [path("bolumler/<int:unit_id>/ekstre-gonder/", views.send_statement, name="send_statement")]
