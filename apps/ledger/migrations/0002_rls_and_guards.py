"""Defter güvenceleri veritabanında: RLS, değiştirilemezlik, denge ve dönem kilidi.

Denge ve satır sayısı kontrolü işlem sonunda (DEFERRABLE INITIALLY DEFERRED) çalışır; böylece
kayıt ve satırları aynı işlemde sırayla eklenebilir. Kontrol, kuruluş bağlamı kapanmış olsa
bile doğru toplamı görmek için yalnızca kendi sorgusu süresince RLS atlamasını açar.
"""

from django.db import migrations

from apps.tenancy.rls import enable_rls

GUARDS = """
CREATE FUNCTION ledger_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Defter kayıtları değiştirilemez ve silinemez (%)', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_entry_immutable BEFORE UPDATE OR DELETE ON ledger_journalentry
    FOR EACH ROW EXECUTE FUNCTION ledger_immutable();
CREATE TRIGGER ledger_entry_no_truncate BEFORE TRUNCATE ON ledger_journalentry
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_immutable();
CREATE TRIGGER ledger_line_immutable BEFORE UPDATE OR DELETE ON ledger_journalline
    FOR EACH ROW EXECUTE FUNCTION ledger_immutable();
CREATE TRIGGER ledger_line_no_truncate BEFORE TRUNCATE ON ledger_journalline
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_immutable();

CREATE FUNCTION ledger_check_entry(p_entry bigint) RETURNS void AS $$
DECLARE
    prev text := current_setting('app.bypass_rls', true);
    n integer;
    diff bigint;
BEGIN
    PERFORM set_config('app.bypass_rls', 'on', true);
    SELECT count(*), coalesce(sum(debit), 0) - coalesce(sum(credit), 0)
      INTO n, diff FROM ledger_journalline WHERE entry_id = p_entry;
    PERFORM set_config('app.bypass_rls', coalesce(prev, 'off'), true);
    IF n < 2 THEN
        RAISE EXCEPTION 'Yevmiye kaydı % en az iki satır içermeli', p_entry;
    END IF;
    IF diff <> 0 THEN
        RAISE EXCEPTION 'Yevmiye kaydı % dengesiz (fark %)', p_entry, diff;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION ledger_check_line_trigger() RETURNS trigger AS $$
BEGIN
    PERFORM ledger_check_entry(NEW.entry_id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION ledger_check_entry_trigger() RETURNS trigger AS $$
BEGIN
    PERFORM ledger_check_entry(NEW.id);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER ledger_line_balanced AFTER INSERT ON ledger_journalline
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_check_line_trigger();
CREATE CONSTRAINT TRIGGER ledger_entry_has_lines AFTER INSERT ON ledger_journalentry
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ledger_check_entry_trigger();

CREATE FUNCTION ledger_period_lock() RETURNS trigger AS $$
DECLARE
    until date;
BEGIN
    SELECT locked_until INTO until FROM ledger_periodlock WHERE site_id = NEW.site_id;
    IF until IS NOT NULL AND NEW.date <= until THEN
        RAISE EXCEPTION 'Dönem % tarihine kadar kilitli; % tarihli kayıt girilemez', until, NEW.date;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_entry_period_lock BEFORE INSERT ON ledger_journalentry
    FOR EACH ROW EXECUTE FUNCTION ledger_period_lock();
"""

DROP = """
DROP TRIGGER IF EXISTS ledger_entry_period_lock ON ledger_journalentry;
DROP FUNCTION IF EXISTS ledger_period_lock();
DROP TRIGGER IF EXISTS ledger_entry_has_lines ON ledger_journalentry;
DROP TRIGGER IF EXISTS ledger_line_balanced ON ledger_journalline;
DROP FUNCTION IF EXISTS ledger_check_entry_trigger();
DROP FUNCTION IF EXISTS ledger_check_line_trigger();
DROP FUNCTION IF EXISTS ledger_check_entry(bigint);
DROP TRIGGER IF EXISTS ledger_line_no_truncate ON ledger_journalline;
DROP TRIGGER IF EXISTS ledger_line_immutable ON ledger_journalline;
DROP TRIGGER IF EXISTS ledger_entry_no_truncate ON ledger_journalentry;
DROP TRIGGER IF EXISTS ledger_entry_immutable ON ledger_journalentry;
DROP FUNCTION IF EXISTS ledger_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]
    operations = [
        enable_rls("ledger_account"),
        enable_rls("ledger_journalentry"),
        enable_rls("ledger_journalline"),
        enable_rls("ledger_periodlock"),
        migrations.RunSQL(GUARDS, DROP),
    ]
