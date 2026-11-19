from django.db import migrations

from apps.tenancy.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [("property", "0001_initial")]
    operations = [
        enable_rls("property_block"),
        enable_rls("property_unittype"),
        enable_rls("property_unit"),
    ]
