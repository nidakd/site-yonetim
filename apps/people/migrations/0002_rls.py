from django.db import migrations

from apps.tenancy.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [("people", "0001_initial")]
    operations = [
        enable_rls("people_person"),
        enable_rls("people_occupancy"),
    ]
