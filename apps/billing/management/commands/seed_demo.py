"""Tamamen SENTETİK bir demo kuruluşu ve sitesi kurar (gerçek kişi verisi yoktur).

    python manage.py seed_demo --admin-email demo@example.test

İki blok, 16 bağımsız bölüm, uydurma malik/kiracılar, özel aidat anlaşmaları, iki aylık aidat
tahakkuku, tahsilatlar (biri avanslı) ve giderler oluşturur. Yöneticiye şifre belirleme bağlantısı
yazdırılır; ilk girişte iki adımlı doğrulama kurulur.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.billing import services as billing
from apps.billing.models import ChargeBatch, ChargeType, DuesAgreement
from apps.core.context import platform_context, tenant_context
from apps.expenses import services as expenses
from apps.expenses.models import Vendor
from apps.ledger.models import Account
from apps.payments import services as payments
from apps.people.models import Occupancy, Person
from apps.property.models import Block, Unit, UnitType
from apps.tenancy.models import Membership, Organization, Role, Site

ORG_NAME = "Demo Site Yönetimi"
FIRST = [
    "Ada",
    "Bora",
    "Cem",
    "Defne",
    "Ece",
    "Fırat",
    "Gül",
    "Hakan",
    "İpek",
    "Kaan",
    "Lale",
    "Mert",
    "Nil",
    "Onur",
    "Pelin",
    "Rüzgar",
    "Selin",
    "Tuna",
    "Umut",
    "Yağmur",
]
LAST = ["Demir", "Kaya", "Yıldız", "Aydın", "Öztürk", "Arslan", "Doğan", "Kılıç", "Aslan", "Çetin"]


class Command(BaseCommand):
    help = "Sentetik demo kuruluşu ve sitesi kurar."

    def add_arguments(self, parser):
        parser.add_argument("--org-name", default=ORG_NAME)
        parser.add_argument("--admin-email", default="demo@example.test")
        parser.add_argument("--base-url", default="http://localhost:8000")

    def handle(self, *args, **opts):
        User = get_user_model()
        email = opts["admin_email"].strip().lower()
        with platform_context(), transaction.atomic():
            name = opts["org_name"]
            if Organization.objects.filter(name=name).exists():
                raise CommandError(f"'{name}' adlı kuruluş zaten var; --org-name ile başka bir ad verin.")
            org = Organization.objects.create(name=name)
            user = User.objects.filter(email=email).first() or User.objects.create_user(
                email, full_name="Demo Yönetici"
            )
            Membership.objects.create(user=user, organization=org, role=Role.ORG_ADMIN)

        with tenant_context(org.pk), transaction.atomic():
            site = Site.objects.create(
                name="Çınar Evleri", city="İstanbul", district="Kadıköy", address="Örnek Mah. Deneme Sok. No 1"
            )
            billing.setup_site(site)
            self._build(site, user)

        self.stdout.write(self.style.SUCCESS(f"Demo kuruldu: {org.name} / Çınar Evleri (kuruluş id={org.pk})"))
        if not user.has_usable_password():
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            path = reverse("accounts:password_reset_confirm", args=[uid, default_token_generator.make_token(user)])
            self.stdout.write(f"Şifre belirleme bağlantısı: {opts['base_url'].rstrip('/')}{path}")

    def _build(self, site, user):
        t21 = UnitType.objects.create(site=site, name="2+1", default_amount=125000)
        t31 = UnitType.objects.create(site=site, name="3+1", default_amount=160000)
        units = []
        for b_index, name in enumerate(["A Blok", "B Blok"]):
            block = Block.objects.create(site=site, name=name, order=b_index)
            for no in range(1, 9):
                big = no % 2 == 0
                units.append(
                    Unit.objects.create(
                        site=site,
                        block=block,
                        number=str(no),
                        floor=(no - 1) // 2,
                        unit_type=t31 if big else t21,
                        gross_area=Decimal("135.00") if big else Decimal("95.00"),
                        share_numerator=75 if big else 50,
                        share_denominator=1000,
                    )
                )
        for i, unit in enumerate(units):
            owner = Person.objects.create(first_name=FIRST[i % 20], last_name=LAST[i % 10], phone=f"0555000{i:04d}")
            Occupancy.objects.create(
                unit=unit, person=owner, relation="owner", start_date=date(2019, 5, 1), is_liable=i % 4 != 1
            )
            if i % 4 == 1:  # her dört daireden biri kirada
                tenant = Person.objects.create(
                    first_name=FIRST[(i + 7) % 20], last_name=LAST[(i + 3) % 10], phone=f"0555100{i:04d}"
                )
                Occupancy.objects.create(unit=unit, person=tenant, relation="tenant", start_date=date(2025, 9, 1))
        DuesAgreement.objects.create(
            unit=units[0],
            kind="exempt",
            valid_from=date(2026, 1, 1),
            decision_ref="Genel kurul 2026/1, md. 5 (yönetici dairesi)",
        )
        DuesAgreement.objects.create(
            unit=units[9],
            kind="discount",
            discount_percent=Decimal("20"),
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            decision_ref="Yönetim kararı 2026/3",
        )

        aidat = ChargeType.objects.get(site=site, name="Aidat")
        for period in (date(2026, 10, 1), date(2026, 11, 1)):
            batch = ChargeBatch.objects.create(
                site=site,
                charge_type=aidat,
                period=period,
                due_date=period.replace(day=10),
                method=ChargeBatch.Method.UNIT_TYPE,
                description=f"{['Ekim', 'Kasım'][period.month - 10]} 2026 aidatı",
                created_by=user,
            )
            billing.approve(batch, user)

        bank = Account.objects.get(site=site, code="102.01")
        cash = Account.objects.get(site=site, code="100")
        for i, unit in enumerate(units):
            if i % 5 == 3:
                continue  # bazı bölümler hiç ödemedi
            liable = unit.occupancies.filter(is_liable=True).first()
            amount = unit.unit_type.default_amount * (2 if i % 3 else 1)
            if i == 6:
                amount += 50000  # fazla ödeme → avans
            payments.create_payment(
                site,
                unit,
                amount=amount,
                day=date(2026, 11, 3 + i % 12),
                channel="transfer" if i % 2 else "cash",
                account=bank if i % 2 else cash,
                payer=liable.person if liable else None,
                user=user,
            )

        vendor = Vendor.objects.create(site=site, name="Örnek Enerji A.Ş.")
        accounts = {a.code: a for a in Account.objects.filter(site=site)}
        for day, code, text, amount, ven in [
            (date(2026, 11, 5), "770.01", "Ekim ortak alan elektriği", 284050, vendor),
            (date(2026, 11, 6), "770.04", "Kasım temizlik hizmeti", 950000, None),
            (date(2026, 11, 12), "770.06", "Asansör periyodik bakım", 420000, None),
        ]:
            expenses.create_expense(
                site,
                day=day,
                category=accounts[code],
                paid_from=bank,
                amount=amount,
                description=text,
                vendor=ven,
                user=user,
            )
        expenses.create_transfer(
            site,
            day=date(2026, 11, 15),
            from_account=cash,
            to_account=bank,
            amount=500000,
            user=user,
            description="Kasadaki nakit bankaya yatırıldı",
        )
