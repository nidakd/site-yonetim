"""PostgreSQL satır seviyesi güvenlik (RLS) yardımcıları.

Her kiracı tablosu için migration'da `enable_rls("tablo")` çalıştırılır. `tests/test_rls.py`
yeni eklenen her kiracı tablosunda RLS'in açık olduğunu denetler; unutulursa test kırılır.
"""

from django.db import migrations

ORG_EXPR = "NULLIF(current_setting('app.current_org', true), '')::bigint"
BYPASS_EXPR = "current_setting('app.bypass_rls', true) = 'on'"


def enable_rls(table: str, column: str = "organization_id", allow_null_insert: bool = False):
    check = f"{BYPASS_EXPR} OR {column} = {ORG_EXPR}"
    if allow_null_insert:
        check += f" OR {column} IS NULL"
    forward = f"""
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON {table}
            USING ({BYPASS_EXPR} OR {column} = {ORG_EXPR})
            WITH CHECK ({check});
    """
    backward = f"""
        DROP POLICY IF EXISTS tenant_isolation ON {table};
        ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
    """
    return migrations.RunSQL(forward, backward)
