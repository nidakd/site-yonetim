"""Sakin girişi ve erişim kuralları.

Erişim kuralı (Plan §4.1): sakin, yöneticinin o telefon numarasıyla kaydettiği ve bugün geçerli olan
malik/kiracı ilişkilerinin bölümlerini görür. İlişki bitince (taşınma, satış) erişim de biter.
Bölümün ödeme ve ekstre geçmişi, sakinin ilişkisinin başladığı tarihten itibaren gösterilir;
önceki malik/kiracının makbuzları görünmez.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.comms.sms import send_sms
from apps.core.context import platform_context
from apps.people.models import Occupancy, active_on
from apps.property.models import Unit

from .models import OtpChallenge, PortalSettings, ResidentAccount

SESSION_KEY = "resident"


class Throttled(Exception):
    pass


def code_hash(phone: str, code: str) -> str:
    return salted_hmac("resident-otp", f"{phone}:{code}").hexdigest()


def _bump(key: str, seconds: int) -> int:
    cache.add(key, 0, seconds)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, seconds)
        return 1


def _hkey(kind: str, value: str) -> str:
    return f"resident:{kind}:{hashlib.sha256(value.encode()).hexdigest()[:32]}"


def _portal_open() -> Q:
    return ~Q(unit__site__portal_settings__portal_enabled=False)


def orgs_for_phone(phone: str, day: date | None = None) -> list[int]:
    """Bu telefonun bugün geçerli bir bölüm ilişkisi olan kuruluşlar.

    Sakin giriş yaparken kuruluş henüz bilinmediği için kiracı izolasyonu bu tek sorgu için
    kaldırılır; yalnızca kuruluş kimlikleri döner, kişi ya da bölüm verisi dönmez."""
    if not phone:
        return []
    day = day or timezone.localdate()
    with platform_context():
        return sorted(
            set(
                Occupancy.unscoped.filter(active_on(day), _portal_open())
                .filter(
                    person__phone=phone,
                    person__is_anonymized=False,
                    unit__site__is_active=True,
                    organization__is_active=True,
                )
                .values_list("organization_id", flat=True)
            )
        )


def request_code(phone: str, ip: str | None) -> bool:
    """Kayıtlı telefona SMS kodu gönderir. Kayıtsız numaraya SMS gitmez ama dışarıdan aynı
    görünür (numaranın sistemde olup olmadığı anlaşılmaz). Sınır aşılırsa `Throttled`."""
    if _bump(_hkey("phone", phone), 15 * 60) > settings.RESIDENT_OTP_PER_PHONE:
        raise Throttled
    if ip and _bump(_hkey("ip", ip), 60 * 60) > settings.RESIDENT_OTP_PER_IP:
        raise Throttled
    if not orgs_for_phone(phone):
        return False
    code = f"{secrets.randbelow(10**6):06d}"
    with transaction.atomic():
        OtpChallenge.objects.filter(phone=phone, consumed_at__isnull=True).update(consumed_at=timezone.now())
        OtpChallenge.objects.create(
            phone=phone,
            code_hash=code_hash(phone, code),
            expires_at=timezone.now() + timedelta(seconds=settings.RESIDENT_OTP_SECONDS),
            ip=ip,
        )
    send_sms(phone, f"Site Yönetim giriş kodunuz: {code}. Kodu kimseyle paylaşmayın; yönetim sizden kod istemez.")
    return True


def verify_code(phone: str, code: str) -> bool:
    code = "".join(ch for ch in code if ch.isdigit())
    with transaction.atomic():
        challenge = (
            OtpChallenge.objects.select_for_update()
            .filter(phone=phone, consumed_at__isnull=True, expires_at__gt=timezone.now())
            .first()
        )
        if challenge is None or challenge.attempts >= settings.RESIDENT_OTP_MAX_TRIES:
            return False
        if not constant_time_compare(challenge.code_hash, code_hash(phone, code)):
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
            return False
        challenge.consumed_at = timezone.now()
        challenge.save(update_fields=["consumed_at"])
    return True


def password_locked(phone: str, ip: str | None) -> bool:
    if cache.get(_hkey("pwfail", phone), 0) >= settings.LOGIN_MAX_FAILURES:
        return True
    return bool(ip) and cache.get(_hkey("pwfail-ip", ip), 0) >= settings.LOGIN_MAX_FAILURES_PER_IP


def password_failed(phone: str, ip: str | None) -> None:
    _bump(_hkey("pwfail", phone), settings.LOGIN_LOCK_SECONDS)
    if ip:
        _bump(_hkey("pwfail-ip", ip), settings.LOGIN_LOCK_SECONDS)


def account_for(phone: str) -> ResidentAccount:
    account, _ = ResidentAccount.objects.get_or_create(phone=phone)
    return account


def start_session(request, account: ResidentAccount, *, remember: bool) -> None:
    request.session.cycle_key()
    request.session[SESSION_KEY] = {"account": account.pk, "phone": account.phone, "org": None}
    if remember and not request.user.is_authenticated:  # yönetici oturumunu uzatma
        request.session.set_expiry(settings.RESIDENT_REMEMBER_DAYS * 24 * 3600)
    account.last_login_at = timezone.now()
    account.save(update_fields=["last_login_at"])


def end_session(request) -> None:
    request.session.pop(SESSION_KEY, None)
    if not request.user.is_authenticated:
        request.session.flush()


# --- kiracı bağlamında: sakinin bölümleri ------------------------------------------------------


@dataclass
class ResidentUnit:
    unit: Unit
    visible_from: date
    relations: list[str] = field(default_factory=list)


def resident_units(phone: str, day: date | None = None) -> list[ResidentUnit]:
    """Kiracı bağlamında çağrılır: telefonun bugün geçerli ilişkisi olan bölümler."""
    day = day or timezone.localdate()
    out: dict[int, ResidentUnit] = {}
    occs = (
        Occupancy.objects.filter(active_on(day), _portal_open())
        .filter(person__phone=phone, person__is_anonymized=False, unit__site__is_active=True)
        .select_related("unit__block", "unit__site")
        .order_by("unit__site__name", "unit__block__order", "unit__block__name", "unit__sort_key", "start_date")
    )
    for occ in occs:
        item = out.get(occ.unit_id)
        if item is None:
            item = out[occ.unit_id] = ResidentUnit(occ.unit, occ.start_date)
        item.visible_from = min(item.visible_from, occ.start_date)
        label = occ.get_relation_display()
        if label not in item.relations:
            item.relations.append(label)
    return list(out.values())


def portal_settings(site) -> PortalSettings:
    obj = PortalSettings.objects.filter(site=site).first()
    return obj or PortalSettings(site=site)


def site_overview(site, settings_obj: PortalSettings, *, today: date | None = None) -> dict:
    """Yönetimin izin verdiği anonim site özeti. Bölüm ya da kişi bazında hiçbir bilgi içermez."""
    from django.db.models import Sum

    from apps.billing.models import Charge
    from apps.expenses.models import Expense
    from apps.ledger.services import account_balances, cash_accounts

    today = today or timezone.localdate()
    out: dict = {"month": today.replace(day=1)}
    if settings_obj.show_collection_rate:
        charges = (
            Charge.objects.filter(site=site, period=out["month"], is_cancelled=False)
            .exclude(batch__method="opening")
            .annotate(paid=Sum("allocations__amount", filter=Q(allocations__is_active=True)))
        )
        accrued = sum(c.amount for c in charges)
        paid = sum(c.paid or 0 for c in charges)
        out["collection"] = {"accrued": accrued, "paid": paid, "rate": round(100 * paid / accrued) if accrued else None}
    if settings_obj.show_cash_balance:
        balances = account_balances(site)
        out["cash_total"] = sum(balances.get(a.pk, 0) for a in cash_accounts(site))
    if settings_obj.show_expenses:
        rows = (
            Expense.objects.filter(site=site, status="valid", date__year=today.year)
            .values("category__name")
            .annotate(total=Sum("amount"))
            .order_by("-total")
        )
        out["expenses"] = [{"name": r["category__name"], "total": r["total"]} for r in rows]
        out["expense_total"] = sum(r["total"] for r in out["expenses"])
    return out
