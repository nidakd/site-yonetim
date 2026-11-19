from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from django.db import models


def _plain(value):
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, date | datetime | Decimal):
        return str(value)
    return value


def snapshot(instance: models.Model, fields: list[str]) -> dict:
    return {f: _plain(getattr(instance, f)) for f in fields}


def changes(before: dict, after: dict, *, redact: set[str] = frozenset()) -> dict:
    """Denetim izi için önce/sonra farkı. Kişisel veri alanları yalnızca 'değişti' olarak yazılır."""
    out = {}
    for key, old in before.items():
        new = after.get(key)
        if old != new:
            out[key] = "değişti" if key in redact else {"eski": old, "yeni": new}
    return out


def month_start(day: date) -> date:
    return day.replace(day=1)


def reserve_id(model) -> int:
    """Kaydı oluşturmadan önce birincil anahtarını ayırır; böylece deftere kaynak kimliğiyle yazılabilir."""
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT nextval(pg_get_serial_sequence(%s, 'id'))", [model._meta.db_table])
        return cur.fetchone()[0]
