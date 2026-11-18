"""Kaba kuvvet denemelerine karşı sınır: e-posta başına ve IP başına başarısız giriş sayacı.

Sayaç önbellekte (geliştirmede bellek, canlıda Redis) tutulur. Kilit süresince doğru şifre de
kabul edilmez; böylece tahmin denemesi kilidin içinden devam edemez.
"""

import hashlib

from django.conf import settings
from django.core.cache import cache


def _key(kind: str, value: str) -> str:
    return f"login-fail:{kind}:{hashlib.sha256(value.lower().encode()).hexdigest()[:32]}"


def is_locked(email: str, ip: str | None) -> bool:
    if cache.get(_key("email", email), 0) >= settings.LOGIN_MAX_FAILURES:
        return True
    return bool(ip) and cache.get(_key("ip", ip), 0) >= settings.LOGIN_MAX_FAILURES_PER_IP


def register_failure(email: str, ip: str | None) -> None:
    for kind, value in (("email", email), ("ip", ip)):
        if not value:
            continue
        key = _key(kind, value)
        cache.add(key, 0, settings.LOGIN_LOCK_SECONDS)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, settings.LOGIN_LOCK_SECONDS)


def clear(email: str) -> None:
    cache.delete(_key("email", email))
