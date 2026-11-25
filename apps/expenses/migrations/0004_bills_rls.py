from django.db import migrations

from apps.tenancy.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [("expenses", "0003_bills")]
    operations = [enable_rls("expenses_bill"), enable_rls("expenses_billpayment")]
