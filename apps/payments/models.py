from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.billing.models import Charge
from apps.ledger.models import Account, JournalEntry
from apps.people.models import Person
from apps.property.models import Unit
from apps.tenancy.models import Site, TenantModel


class Payment(TenantModel):
    """Tahsilat. Borçlarla eşleşmeyen kısmı avanstır; yeni tahakkukta otomatik mahsup edilir."""

    class Channel(models.TextChoices):
        CASH = "cash", "Nakit"
        TRANSFER = "transfer", "Havale / EFT"
        CARD = "card", "Kredi kartı"
        OTHER = "other", "Diğer"

    class Status(models.TextChoices):
        VALID = "valid", "Geçerli"
        CANCELLED = "cancelled", "İptal"

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="payments")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="payments")
    payer = models.ForeignKey(Person, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    payer_name = models.CharField("ödeyen", max_length=160, blank=True)  # makbuz için anlık görüntü
    date = models.DateField("tarih")
    amount = models.BigIntegerField("tutar")
    channel = models.CharField("ödeme şekli", max_length=20, choices=Channel.choices)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="kasa / banka")
    reference = models.CharField("dekont / referans", max_length=120, blank=True)
    note = models.CharField("not", max_length=255, blank=True)
    receipt_year = models.PositiveSmallIntegerField()
    receipt_number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.VALID)
    entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name="+")
    cancel_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cancel_reason = models.CharField("iptal nedeni", max_length=255, blank=True)

    class Meta:
        verbose_name = "tahsilat"
        verbose_name_plural = "tahsilatlar"
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
            models.UniqueConstraint(fields=["site", "receipt_year", "receipt_number"], name="payment_unique_receipt"),
        ]

    @property
    def receipt_no(self) -> str:
        return f"{self.receipt_year}-{self.receipt_number:06d}"

    def __str__(self) -> str:
        return f"Makbuz {self.receipt_no}"


class Allocation(TenantModel):
    """Ödemenin hangi borcu ne kadar kapattığı. Geri alınınca pasifleşir (iz kalır)."""

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name="allocations")
    amount = models.BigIntegerField()
    from_advance = models.BooleanField(default=False)  # sonradan avanstan mahsup edildi
    is_active = models.BooleanField(default=True)
    reversed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "eşleşme"
        verbose_name_plural = "eşleşmeler"
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="allocation_amount_positive")]


class ReceiptSequence(TenantModel):
    """Site ve yıl başına boşluksuz makbuz numarası (satır kilidiyle artırılır)."""

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="+")
    year = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "year"], name="receipt_sequence_unique")]
