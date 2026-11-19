from django.db import migrations

from apps.tenancy.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [("payments", "0001_initial")]
    operations = [
        enable_rls("payments_payment"),
        enable_rls("payments_allocation"),
        enable_rls("payments_receiptsequence"),
    ]
