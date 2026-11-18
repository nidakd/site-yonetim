"""Yeni müşteri kuruluşu ve ilk yöneticisini oluşturur; yöneticiye şifre belirleme bağlantısı üretir.

    python manage.py create_organization --name "Örnek Site Yönetimi" \
        --admin-email yonetici@example.com --admin-name "Ad Soyad"
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.audit.services import record
from apps.core.context import platform_context, tenant_context
from apps.tenancy.models import Membership, Organization, Role


class Command(BaseCommand):
    help = "Kuruluş ve ilk kuruluş yöneticisini oluşturur."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument(
            "--kind", choices=[k for k, _ in Organization.Kind.choices], default=Organization.Kind.SINGLE_SITE
        )
        parser.add_argument("--admin-email", required=True)
        parser.add_argument("--admin-name", required=True)
        parser.add_argument("--base-url", default="http://localhost:8000")

    def handle(self, *args, **opts):
        User = get_user_model()
        email = opts["admin_email"].strip().lower()
        with platform_context(), transaction.atomic():
            if Organization.objects.filter(name=opts["name"]).exists():
                raise CommandError("Bu adla bir kuruluş zaten var.")
            org = Organization.objects.create(name=opts["name"], kind=opts["kind"])
            user = User.objects.filter(email=email).first()
            created = user is None
            if created:
                user = User.objects.create_user(email, full_name=opts["admin_name"])
            Membership.objects.create(user=user, organization=org, role=Role.ORG_ADMIN)
        with tenant_context(org.pk):
            record("org.created", org, {"name": org.name, "kind": org.kind}, organization_id=org.pk)
            record("member.added", user, {"role": Role.ORG_ADMIN}, organization_id=org.pk)

        self.stdout.write(self.style.SUCCESS(f"Kuruluş oluşturuldu: {org.name} (id={org.pk})"))
        if created or not user.has_usable_password():
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            path = reverse("accounts:password_reset_confirm", args=[uid, token])
            self.stdout.write("Yöneticinin şifre belirleme bağlantısı (tek kullanımlık, 3 gün geçerli):")
            self.stdout.write(f"  {opts['base_url'].rstrip('/')}{path}")
        else:
            self.stdout.write(f"{email} mevcut bir kullanıcı; kuruluşa yönetici olarak eklendi.")
