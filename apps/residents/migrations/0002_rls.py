from django.db import migrations

from apps.tenancy.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [("residents", "0001_initial")]
    operations = [
        enable_rls("residents_residentconsent"),
        enable_rls("residents_portalsettings"),
    ]
