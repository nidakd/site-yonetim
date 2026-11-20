"""E-posta bildirimleri: makbuz ve hesap ekstresi.

Alıcılar: bölümün bugün geçerli malik/kiracılarından e-postası olan ve "bildirim alır" işaretli
olanlar. Makbuzda ödeyen kişi belliyse yalnızca ona gider. Gönderim hatası tahsilatı bozmaz;
sonuç `Notification` olarak kaydedilir ve bölüm sayfasında görünür.
"""

from __future__ import annotations

import logging
import smtplib

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from apps.billing.services import open_charges
from apps.people.models import Occupancy, active_on
from apps.reports.services import unit_balances, unit_statement

from .models import Notification

logger = logging.getLogger("comms")


def recipients(unit, *, person=None) -> list:
    occs = (
        Occupancy.objects.filter(active_on(timezone.localdate()), unit=unit, receives_notifications=True)
        .exclude(person__email="")
        .filter(person__is_anonymized=False)
        .select_related("person")
    )
    people = {o.person_id: o.person for o in occs}
    if person is not None:
        return [people[person.pk]] if person.pk in people else []
    return list(people.values())


def _send(site, unit, person, kind: str, subject: str, body: str, *, user=None, source_id=None) -> Notification:
    status, error = Notification.Status.SENT, ""
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [person.email], fail_silently=False)
    except (smtplib.SMTPException, OSError) as exc:
        status, error = Notification.Status.FAILED, type(exc).__name__
        logger.warning("E-posta gönderilemedi (%s)", error)
    return Notification.objects.create(
        site=site,
        unit=unit,
        person=person,
        kind=kind,
        recipient=person.email,
        subject=subject[:200],
        status=status,
        error=error,
        source_id=source_id,
        created_by=user,
    )


def portal_url(request=None) -> str:
    from django.urls import reverse

    path = reverse("residents:login")
    return request.build_absolute_uri(path) if request is not None else path


def send_receipt(payment, *, user=None, request=None) -> list[Notification]:
    if payment.is_opening:
        return []
    unit, site = payment.unit, payment.site
    people = recipients(unit, person=payment.payer) if payment.payer_id else recipients(unit)
    subject = f"{site.name} · {unit.label} · makbuz {payment.receipt_no}"
    allocations = payment.allocations.filter(is_active=True, from_advance=False).select_related("charge")
    out = []
    for person in people:
        body = render_to_string(
            "comms/receipt_email.txt",
            {
                "site": site,
                "unit": unit,
                "payment": payment,
                "person": person,
                "allocations": allocations,
                "advance": payment.amount - sum(a.amount for a in allocations),
                "portal_url": portal_url(request),
            },
        )
        out.append(_send(site, unit, person, Notification.Kind.RECEIPT, subject, body, user=user, source_id=payment.pk))
    return out


def send_statement(unit, *, user=None, request=None) -> list[Notification]:
    site = unit.site
    people = recipients(unit)
    charges = open_charges(unit)
    balance = unit_balances(site, [unit.pk]).get(unit.pk, 0)
    subject = f"{site.name} · {unit.label} · hesap durumu"
    out = []
    for person in people:
        start = (
            Occupancy.objects.filter(unit=unit, person=person)
            .order_by("start_date")
            .values_list("start_date", flat=True)
        ).first()
        body = render_to_string(
            "comms/statement_email.txt",
            {
                "site": site,
                "unit": unit,
                "person": person,
                "charges": charges,
                "balance": balance,
                "rows": unit_statement(unit, start=start)[-12:],
                "portal_url": portal_url(request),
                "today": timezone.localdate(),
            },
        )
        out.append(_send(site, unit, person, Notification.Kind.STATEMENT, subject, body, user=user))
    return out
