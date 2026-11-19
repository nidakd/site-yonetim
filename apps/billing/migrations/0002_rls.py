from django.db import migrations

from apps.tenancy.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [
        enable_rls("billing_chargetype"),
        enable_rls("billing_duesagreement"),
        enable_rls("billing_chargebatch"),
        enable_rls("billing_charge"),
    ]
