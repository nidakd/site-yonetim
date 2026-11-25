from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.ledger.models import Account, JournalEntry
from apps.tenancy.models import Site, TenantModel


class Vendor(TenantModel):
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="vendors")
    name = models.CharField("ad / unvan", max_length=160)
    tax_number = models.CharField("vergi no", max_length=20, blank=True)
    phone = models.CharField("telefon", max_length=20, blank=True)

    class Meta:
        verbose_name = "tedarikçi"
        verbose_name_plural = "tedarikçiler"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["site", "name"], name="vendor_unique_name")]

    def __str__(self) -> str:
        return self.name


class Expense(TenantModel):
    """Ödenmiş gider: gider hesabı borç, kasa/banka alacak."""

    class Status(models.TextChoices):
        VALID = "valid", "Geçerli"
        CANCELLED = "cancelled", "İptal"

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="expenses")
    date = models.DateField("tarih")
    category = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="gider kalemi")
    vendor = models.ForeignKey(
        Vendor, on_delete=models.PROTECT, null=True, blank=True, related_name="expenses", verbose_name="tedarikçi"
    )
    description = models.CharField("açıklama", max_length=200)
    document_number = models.CharField("belge no", max_length=60, blank=True)
    amount = models.BigIntegerField("tutar")
    paid_from = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="ödendiği hesap")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.VALID)
    entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name="+")
    cancel_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    cancel_reason = models.CharField("iptal nedeni", max_length=255, blank=True)

    class Meta:
        verbose_name = "gider"
        verbose_name_plural = "giderler"
        ordering = ["-date", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="expense_amount_positive")]

    def __str__(self) -> str:
        return self.description


class Transfer(TenantModel):
    """Kasa ↔ banka virmanı."""

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="transfers")
    date = models.DateField("tarih")
    from_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="çıkan hesap")
    to_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="giren hesap")
    amount = models.BigIntegerField("tutar")
    description = models.CharField("açıklama", max_length=200, blank=True)
    entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")

    class Meta:
        verbose_name = "virman"
        verbose_name_plural = "virmanlar"
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="transfer_amount_positive"),
            models.CheckConstraint(condition=~Q(from_account=F("to_account")), name="transfer_distinct_accounts"),
        ]


class Bill(TenantModel):
    """Ödenecek fatura: gelince gider hesabı borç, satıcılara borçlar alacak; ödeme bu borcu kapatır."""

    class Status(models.TextChoices):
        VALID = "valid", "Geçerli"
        CANCELLED = "cancelled", "İptal"

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="bills")
    date = models.DateField("fatura tarihi")
    due_date = models.DateField("son ödeme tarihi", null=True, blank=True)
    category = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="gider kalemi")
    vendor = models.ForeignKey(
        Vendor, on_delete=models.PROTECT, null=True, blank=True, related_name="bills", verbose_name="tedarikçi"
    )
    description = models.CharField("açıklama", max_length=200)
    document_number = models.CharField("belge no", max_length=60, blank=True)
    amount = models.BigIntegerField("tutar")
    paid_amount = models.BigIntegerField("ödenen", default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.VALID)
    entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name="+")
    cancel_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    cancel_reason = models.CharField("iptal nedeni", max_length=255, blank=True)

    class Meta:
        verbose_name = "ödenecek fatura"
        verbose_name_plural = "ödenecek faturalar"
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="bill_amount_positive"),
            models.CheckConstraint(
                condition=Q(paid_amount__gte=0) & Q(paid_amount__lte=F("amount")), name="bill_paid_within_amount"
            ),
        ]

    @property
    def remaining(self) -> int:
        return self.amount - self.paid_amount

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.VALID and self.remaining > 0

    def __str__(self) -> str:
        return self.description


class BillPayment(TenantModel):
    """Faturanın (bir kısmının) kasadan/bankadan ödenmesi: satıcılara borçlar borç, kasa/banka alacak."""

    class Status(models.TextChoices):
        VALID = "valid", "Geçerli"
        CANCELLED = "cancelled", "İptal"

    bill = models.ForeignKey(Bill, on_delete=models.PROTECT, related_name="payments")
    date = models.DateField("ödeme tarihi")
    paid_from = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="ödendiği hesap")
    amount = models.BigIntegerField("tutar")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.VALID)
    entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name="+")
    cancel_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    cancel_reason = models.CharField("iptal nedeni", max_length=255, blank=True)

    class Meta:
        verbose_name = "fatura ödemesi"
        verbose_name_plural = "fatura ödemeleri"
        ordering = ["date", "id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="bill_payment_amount_positive")]
