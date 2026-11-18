from django.db import migrations

from apps.tenancy.rls import enable_rls

APPEND_ONLY = """
CREATE FUNCTION audit_event_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditevent kayıtları değiştirilemez ve silinemez';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_auditevent
    FOR EACH ROW EXECUTE FUNCTION audit_event_append_only();

CREATE TRIGGER audit_event_no_truncate
    BEFORE TRUNCATE ON audit_auditevent
    FOR EACH STATEMENT EXECUTE FUNCTION audit_event_append_only();
"""

DROP = """
DROP TRIGGER IF EXISTS audit_event_no_truncate ON audit_auditevent;
DROP TRIGGER IF EXISTS audit_event_no_update_delete ON audit_auditevent;
DROP FUNCTION IF EXISTS audit_event_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [
        # Kuruluşsuz kayıtlar (ör. bilinmeyen e-postayla deneme) yazılabilir ama yalnızca platform okur.
        enable_rls("audit_auditevent", allow_null_insert=True),
        migrations.RunSQL(APPEND_ONLY, DROP),
    ]
