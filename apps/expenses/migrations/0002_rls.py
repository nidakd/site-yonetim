from django.db import migrations

from apps.tenancy.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [("expenses", "0001_initial")]
    operations = [
        enable_rls("expenses_vendor"),
        enable_rls("expenses_expense"),
        enable_rls("expenses_transfer"),
    ]
