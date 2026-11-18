"""İstek bağlamı: şu an hangi kuruluş (kiracı) adına, kim, hangi IP'den işlem yapılıyor.

Kiracı bağlamı iki yere birden yazılır:
1. Python tarafı (contextvar): ORM yöneticileri sorguları bu kuruluşla sınırlar.
2. PostgreSQL oturumu (`app.current_org`): satır seviyesi güvenlik (RLS) politikaları bunu okur.
   Kod tarafında bir filtre unutulsa bile veritabanı başka kuruluşun satırını döndürmez.

Bağlam yoksa tenant tablolarına erişim başarısız olur (fail-closed).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import connection

_org: ContextVar[int | None] = ContextVar("org_id", default=None)
_bypass: ContextVar[bool] = ContextVar("bypass_rls", default=False)
_actor_id: ContextVar[int | None] = ContextVar("actor_id", default=None)
_ip: ContextVar[str | None] = ContextVar("ip", default=None)


class TenantContextMissing(RuntimeError):
    """Kiracıya ait veriye kuruluş bağlamı olmadan erişilmeye çalışıldı."""


def current_org_id() -> int | None:
    return _org.get()


def require_org_id() -> int:
    org_id = _org.get()
    if org_id is None and not _bypass.get():
        raise TenantContextMissing("Kuruluş bağlamı yok; tenant_context() ya da TenantMiddleware gerekli.")
    return org_id  # type: ignore[return-value]


def bypass_active() -> bool:
    return _bypass.get()


def _sync_db() -> None:
    org_id = _org.get()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.current_org', %s, false), set_config('app.bypass_rls', %s, false)",
            ["" if org_id is None else str(org_id), "on" if _bypass.get() else "off"],
        )


@contextmanager
def tenant_context(org_id: int) -> Iterator[None]:
    """Bloğun içinde tüm sorgular yalnızca `org_id` kuruluşunu görür."""
    token_org, token_bypass = _org.set(int(org_id)), _bypass.set(False)
    _sync_db()
    try:
        yield
    finally:
        _org.reset(token_org)
        _bypass.reset(token_bypass)
        _sync_db()


@contextmanager
def platform_context() -> Iterator[None]:
    """Yalnızca platform işleri için (yönetim komutları, kuruluş açma). Kiracı izolasyonunu kaldırır;
    istek işleyen kodda kullanılmaz."""
    token_org, token_bypass = _org.set(None), _bypass.set(True)
    _sync_db()
    try:
        yield
    finally:
        _org.reset(token_org)
        _bypass.reset(token_bypass)
        _sync_db()


def set_request_actor(user_id: int | None, ip: str | None):
    return _actor_id.set(user_id), _ip.set(ip)


def reset_request_actor(tokens) -> None:
    _actor_id.reset(tokens[0])
    _ip.reset(tokens[1])


def current_actor_id() -> int | None:
    return _actor_id.get()


def current_ip() -> str | None:
    return _ip.get()
