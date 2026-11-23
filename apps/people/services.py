"""Kişi anonimleştirme (KVKK, Plan §3.2): ilişkisi bitmiş kişinin iletişim verisi silinir,
muhasebe kayıtları bozulmaz.

- Kişi kaydı: ad "Anonim", soyad "kişi #<no>", telefon, e-posta ve not silinir.
- Tahakkuktaki "sorumlu kişi" anlık görüntülerindeki ad silinir.
- Bildirim kayıtlarındaki e-posta adresi silinir.
- Bu telefon başka hiçbir kişi kaydında yoksa sakin hesabı (ve giriş kodları) silinir.
- Makbuzdaki "ödeyen" adı kalır: makbuz muhasebe belgesidir ve yasal saklama süresine tabidir.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit.services import record
from apps.core.context import platform_context

from .models import Occupancy, Person


class AnonymizeError(ValueError):
    pass


def can_anonymize(person: Person) -> bool:
    today = timezone.localdate()
    active = Occupancy.objects.filter(person=person).exclude(end_date__lt=today)
    return not person.is_anonymized and not active.exists()


def anonymize(person: Person, *, site_id: int | None = None) -> Person:
    from apps.billing.models import Charge
    from apps.comms.models import Notification
    from apps.residents.models import OtpChallenge, ResidentAccount

    if not can_anonymize(person):
        raise AnonymizeError("Yalnızca tüm bölüm ilişkileri bitmiş kişi anonimleştirilebilir.")
    phone = person.phone
    with transaction.atomic():
        person.first_name, person.last_name = "Anonim", f"kişi #{person.pk}"
        person.phone = person.email = person.notes = ""
        person.is_anonymized = True
        person.save()
        for charge in Charge.objects.filter(responsible__contains=[{"person_id": person.pk}]):
            charge.responsible = [
                {**r, "name": "Anonim kişi"} if r.get("person_id") == person.pk else r for r in charge.responsible
            ]
            charge.save(update_fields=["responsible", "updated_at"])
        Notification.objects.filter(person=person).update(recipient="—")
        if phone:
            with platform_context():
                still_used = Person.unscoped.filter(phone=phone, is_anonymized=False).exists()
            if not still_used:
                ResidentAccount.objects.filter(phone=phone).delete()
                OtpChallenge.objects.filter(phone=phone).delete()
        record("person.anonymized", person, site_id=site_id)
    return person


def find_or_create_person(first: str, last: str, phone: str = "", email: str = "") -> tuple[Person, bool]:
    """Aynı ad + soyad + telefon kayıtlıysa o kişiyi döndürür (iki dairenin maliki); telefonsuzlar hep yeni."""
    first, last = " ".join(first.split()), " ".join(last.split())
    if phone:
        existing = Person.objects.filter(
            phone=phone, first_name__iexact=first, last_name__iexact=last, is_anonymized=False
        ).first()
        if existing:
            if email and not existing.email:
                existing.email = email
                existing.save(update_fields=["email", "updated_at"])
            return existing, False
    return Person.objects.create(first_name=first, last_name=last, phone=phone, email=email), True
