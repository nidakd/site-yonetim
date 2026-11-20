from django.conf import settings
from django.db import models

from apps.people.models import Person
from apps.property.models import Unit
from apps.tenancy.models import Site, TenantModel


class Notification(TenantModel):
    """Gönderilen her bildirimin kaydı (kime, ne, ne zaman, sonuç). İçerik saklanmaz; yalnızca konu."""

    class Channel(models.TextChoices):
        EMAIL = "email", "E-posta"

    class Kind(models.TextChoices):
        RECEIPT = "receipt", "Makbuz"
        STATEMENT = "statement", "Hesap ekstresi"

    class Status(models.TextChoices):
        SENT = "sent", "Gönderildi"
        FAILED = "failed", "Gönderilemedi"

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="notifications")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, null=True, blank=True, related_name="notifications")
    person = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.EMAIL)
    kind = models.CharField("tür", max_length=20, choices=Kind.choices)
    recipient = models.CharField("alıcı", max_length=254)
    subject = models.CharField("konu", max_length=200)
    status = models.CharField(max_length=20, choices=Status.choices)
    error = models.CharField(max_length=255, blank=True)
    source_id = models.BigIntegerField(null=True, blank=True)  # makbuzda tahsilat kimliği
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "bildirim"
        verbose_name_plural = "bildirimler"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.recipient}"
