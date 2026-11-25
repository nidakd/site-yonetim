"""Çift taraflı defter. Her iş olayı dengeli bir yevmiye kaydı üretir.

Veritabanı güvenceleri (bkz. migration 0002):
- Kayıt ve satırlar değiştirilemez, silinemez (tetikleyici). Hata ters kayıtla düzeltilir.
- Her kayıtta Σborç = Σalacak (işlem sonunda denetlenen ertelenmiş kısıt tetikleyicisi).
- Kilitli döneme tarihli kayıt girilemez.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.tenancy.models import Site, TenantModel


class Account(TenantModel):
    class Kind(models.TextChoices):
        ASSET = "asset", "Varlık"
        LIABILITY = "liability", "Kaynak (borç)"
        EQUITY = "equity", "Özkaynak"
        INCOME = "income", "Gelir"
        EXPENSE = "expense", "Gider"

    class Role(models.TextChoices):
        """Sistemin otomatik kayıtta kullandığı hesaplar."""

        RECEIVABLES = "receivables", "Sakin alacakları"
        ADVANCES = "advances", "Alınan avanslar"
        OPENING = "opening", "Açılış bakiyeleri"
        PAYABLES = "payables", "Satıcılara borçlar"

    class CashKind(models.TextChoices):
        CASH = "cash", "Kasa"
        BANK = "bank", "Banka"

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="accounts")
    code = models.CharField("hesap kodu", max_length=20)
    name = models.CharField("hesap adı", max_length=120)
    kind = models.CharField("tür", max_length=20, choices=Kind.choices)
    role = models.CharField(max_length=20, choices=Role.choices, null=True, blank=True, editable=False)
    cash_kind = models.CharField("kasa/banka", max_length=10, choices=CashKind.choices, null=True, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    is_active = models.BooleanField("aktif", default=True)

    class Meta:
        verbose_name = "hesap"
        verbose_name_plural = "hesaplar"
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["site", "code"], name="account_unique_code"),
            models.UniqueConstraint(
                fields=["site", "role"], condition=Q(role__isnull=False), name="account_unique_role"
            ),
            models.CheckConstraint(condition=Q(cash_kind__isnull=True) | Q(kind="asset"), name="account_cash_is_asset"),
        ]

    @property
    def is_debit_normal(self) -> bool:
        return self.kind in (self.Kind.ASSET, self.Kind.EXPENSE)

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class JournalEntry(TenantModel):
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="journal_entries")
    date = models.DateField("tarih", db_index=True)
    description = models.CharField("açıklama", max_length=255)
    source_type = models.CharField(max_length=40, blank=True)
    source_id = models.BigIntegerField(null=True, blank=True)
    reverses = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversed_by", editable=False
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "yevmiye kaydı"
        verbose_name_plural = "yevmiye kayıtları"
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["source_type", "source_id"])]

    def __str__(self) -> str:
        return f"#{self.pk} {self.date:%d.%m.%Y} {self.description}"


class JournalLine(TenantModel):
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    unit = models.ForeignKey("property.Unit", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    person = models.ForeignKey("people.Person", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    debit = models.BigIntegerField("borç", default=0)
    credit = models.BigIntegerField("alacak", default=0)
    memo = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "yevmiye satırı"
        verbose_name_plural = "yevmiye satırları"
        indexes = [models.Index(fields=["account", "unit"])]
        constraints = [
            models.CheckConstraint(
                condition=(Q(debit__gt=0) & Q(credit=0)) | (Q(debit=0) & Q(credit__gt=0)), name="line_one_side"
            ),
        ]


class PeriodLock(TenantModel):
    """Bu tarih ve öncesine kayıt girilemez (ay kapanışı)."""

    site = models.OneToOneField(Site, on_delete=models.PROTECT, related_name="period_lock")
    locked_until = models.DateField("kilitli son tarih")
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")

    class Meta:
        verbose_name = "dönem kilidi"
        verbose_name_plural = "dönem kilitleri"
