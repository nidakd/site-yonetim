from django.conf import settings
from django.db import models
from django.db.models import Q, Sum

from apps.ledger.models import Account, JournalEntry
from apps.property.models import Block, Unit
from apps.tenancy.models import Site, TenantModel


class ChargeType(TenantModel):
    """Borç türü: aidat, yakıt, özel toplama... Öncelik, aynı vadede hangisinin önce kapanacağını belirler."""

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="charge_types")
    name = models.CharField("ad", max_length=80)
    income_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="gelir hesabı")
    priority = models.PositiveSmallIntegerField("öncelik", default=50, help_text="Küçük olan önce kapanır")
    apply_agreements = models.BooleanField(
        "özel aidat anlaşmaları uygulanır", default=False, help_text="Sabit tutar, indirim ve muafiyetler"
    )
    once_per_period = models.BooleanField(
        "dönemde bir kez", default=True, help_text="Aynı bölüm aynı ay bu türden iki kez borçlandırılamaz"
    )
    is_active = models.BooleanField("aktif", default=True)

    class Meta:
        verbose_name = "borç türü"
        verbose_name_plural = "borç türleri"
        ordering = ["priority", "name"]
        constraints = [models.UniqueConstraint(fields=["site", "name"], name="chargetype_unique_name")]

    def __str__(self) -> str:
        return self.name


class DuesAgreement(TenantModel):
    """Bölüme özel aidat: sabit tutar, yüzde indirim ya da muafiyet; tarihli ve karara dayalı."""

    class Kind(models.TextChoices):
        FIXED = "fixed", "Sabit tutar"
        DISCOUNT = "discount", "İndirim"
        EXEMPT = "exempt", "Muaf"

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="agreements")
    kind = models.CharField("tür", max_length=20, choices=Kind.choices)
    amount = models.BigIntegerField("tutar (kuruş)", null=True, blank=True)
    discount_percent = models.DecimalField("indirim (%)", max_digits=5, decimal_places=2, null=True, blank=True)
    valid_from = models.DateField("başlangıç")
    valid_to = models.DateField("bitiş", null=True, blank=True)
    decision_ref = models.CharField("karar referansı", max_length=120, blank=True)
    note = models.CharField("not", max_length=255, blank=True)

    class Meta:
        verbose_name = "özel aidat anlaşması"
        verbose_name_plural = "özel aidat anlaşmaları"
        constraints = [
            models.CheckConstraint(
                condition=(Q(kind="fixed") & Q(amount__gte=0))
                | (Q(kind="discount") & Q(discount_percent__gt=0) & Q(discount_percent__lte=100))
                | Q(kind="exempt"),
                name="agreement_values",
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gte=models.F("valid_from")), name="agreement_dates"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.unit} · {self.get_kind_display()}"


class ChargeBatch(TenantModel):
    """Toplu borçlandırma (tahakkuk). Taslak → onay; onaylı tahakkukun iptali ters kayıttır."""

    class Method(models.TextChoices):
        PER_UNIT = "per_unit", "Her bölüme aynı tutar"
        UNIT_TYPE = "unit_type", "Bölüm tipine göre"
        EQUAL = "equal", "Toplamı eşit böl"
        SHARE = "share", "Toplamı arsa payına göre böl"
        AREA = "area", "Toplamı m²'ye göre böl"
        OPENING = "opening", "Açılış bakiyesi"  # kurulumda devreden borçlar; sihirbazda seçilmez

    class Status(models.TextChoices):
        DRAFT = "draft", "Taslak"
        APPROVED = "approved", "Onaylı"
        CANCELLED = "cancelled", "İptal"

    TOTAL_METHODS = {Method.EQUAL, Method.SHARE, Method.AREA}

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="charge_batches")
    charge_type = models.ForeignKey(ChargeType, on_delete=models.PROTECT, related_name="batches")
    period = models.DateField("dönem")  # ayın ilk günü
    due_date = models.DateField("son ödeme")
    method = models.CharField("dağıtım", max_length=20, choices=Method.choices)
    amount = models.BigIntegerField("tutar (kuruş)", null=True, blank=True)  # birim ya da toplam
    block = models.ForeignKey(Block, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    description = models.CharField("açıklama", max_length=200)
    decision_ref = models.CharField("karar referansı", max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    total = models.BigIntegerField(default=0)
    unit_count = models.PositiveIntegerField(default=0)
    entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cancel_reason = models.CharField("iptal nedeni", max_length=255, blank=True)

    class Meta:
        verbose_name = "tahakkuk"
        verbose_name_plural = "tahakkuklar"
        ordering = ["-period", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte=0), name="batch_amount_non_negative"),
            models.CheckConstraint(condition=Q(block__isnull=True) | Q(unit__isnull=True), name="batch_one_scope"),
        ]

    @property
    def scope_label(self) -> str:
        if self.unit_id:
            return self.unit.label
        if self.block_id:
            return self.block.name
        return "Tüm site"

    def __str__(self) -> str:
        return self.description


class Charge(TenantModel):
    """Tek bir borç kalemi. Kalan = tutar − etkin eşleşmeler."""

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="charges")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="charges")
    charge_type = models.ForeignKey(ChargeType, on_delete=models.PROTECT, related_name="charges")
    batch = models.ForeignKey(ChargeBatch, on_delete=models.PROTECT, related_name="charges")
    period = models.DateField("dönem")
    due_date = models.DateField("son ödeme")
    amount = models.BigIntegerField("tutar")
    base_amount = models.BigIntegerField("anlaşma öncesi tutar")
    agreement = models.ForeignKey(DuesAgreement, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    description = models.CharField("açıklama", max_length=200)
    responsible = models.JSONField("sorumlu kişiler", default=list, blank=True)  # dönem başındaki anlık görüntü
    is_cancelled = models.BooleanField(default=False)
    period_unique = models.BooleanField(default=True, editable=False)  # türün "dönemde bir kez" ayarı

    class Meta:
        verbose_name = "borç"
        verbose_name_plural = "borçlar"
        ordering = ["due_date", "charge_type__priority", "id"]
        indexes = [models.Index(fields=["unit", "is_cancelled"])]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="charge_amount_positive"),
            # Aynı bölüm aynı dönemde aynı türden iki kez borçlandırılamaz (iptal edilenler hariç).
            models.UniqueConstraint(
                fields=["unit", "charge_type", "period"],
                condition=Q(is_cancelled=False, period_unique=True),
                name="charge_unique_per_period",
            ),
        ]

    def paid_amount(self) -> int:
        return self.allocations.filter(is_active=True).aggregate(s=Sum("amount"))["s"] or 0

    def __str__(self) -> str:
        return f"{self.unit} · {self.description}"
