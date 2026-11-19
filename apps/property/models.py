from fractions import Fraction

from django.db import models
from django.db.models import Q

from apps.tenancy.models import Site, TenantModel


class Block(TenantModel):
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="blocks")
    name = models.CharField("blok adı", max_length=60)
    order = models.PositiveSmallIntegerField("sıra", default=0)

    class Meta:
        verbose_name = "blok"
        verbose_name_plural = "bloklar"
        ordering = ["order", "name"]
        constraints = [models.UniqueConstraint(fields=["site", "name"], name="block_unique_name_per_site")]

    def __str__(self) -> str:
        return self.name


class UnitType(TenantModel):
    """Bölüm tipi (ör. 2+1, 3+1, dükkan). Basit sitelerde aidat bölüm tipine göre belirlenir."""

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="unit_types")
    name = models.CharField("tip adı", max_length=60)
    default_amount = models.BigIntegerField(
        "aylık aidat (kuruş)", null=True, blank=True, help_text="Bölüm tipine göre aidat dağıtımında kullanılır"
    )

    class Meta:
        verbose_name = "bölüm tipi"
        verbose_name_plural = "bölüm tipleri"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["site", "name"], name="unittype_unique_name_per_site"),
            models.CheckConstraint(condition=Q(default_amount__gte=0), name="unittype_amount_non_negative"),
        ]

    def __str__(self) -> str:
        return self.name


class Unit(TenantModel):
    """Bağımsız bölüm. Silinmez, arşivlenir; arşivli bölüme tahakkuk yapılmaz ama geçmişi görünür."""

    class Kind(models.TextChoices):
        APARTMENT = "apartment", "Daire"
        SHOP = "shop", "Dükkan"
        OFFICE = "office", "Ofis"
        STORAGE = "storage", "Depo"
        PARKING = "parking", "Otopark"

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="units")
    block = models.ForeignKey(Block, on_delete=models.PROTECT, null=True, blank=True, related_name="units")
    number = models.CharField("bölüm no", max_length=20)
    floor = models.SmallIntegerField("kat", null=True, blank=True)
    kind = models.CharField("tür", max_length=20, choices=Kind.choices, default=Kind.APARTMENT)
    unit_type = models.ForeignKey(
        UnitType, on_delete=models.PROTECT, null=True, blank=True, related_name="units", verbose_name="bölüm tipi"
    )
    gross_area = models.DecimalField("brüt alan (m²)", max_digits=8, decimal_places=2, null=True, blank=True)
    share_numerator = models.PositiveIntegerField("arsa payı", null=True, blank=True)
    share_denominator = models.PositiveIntegerField("arsa payı paydası", null=True, blank=True)
    is_archived = models.BooleanField("arşivde", default=False)

    class Meta:
        verbose_name = "bağımsız bölüm"
        verbose_name_plural = "bağımsız bölümler"
        ordering = ["block__order", "block__name", "sort_key", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["site", "block", "number"], name="unit_unique_number", nulls_distinct=False
            ),
            models.CheckConstraint(
                condition=Q(share_numerator__isnull=True, share_denominator__isnull=True)
                | Q(share_numerator__gt=0, share_denominator__gt=0, share_numerator__lte=models.F("share_denominator")),
                name="unit_share_valid",
            ),
            models.CheckConstraint(condition=Q(gross_area__gt=0), name="unit_area_positive"),
        ]

    sort_key = models.PositiveIntegerField(default=0, editable=False)  # "2" < "10" sıralaması için

    def save(self, *args, **kwargs):
        digits = "".join(c for c in self.number if c.isdigit())
        self.sort_key = int(digits[:9]) if digits else 0
        super().save(*args, **kwargs)

    @property
    def label(self) -> str:
        return f"{self.block.name} · {self.number}" if self.block_id else f"No {self.number}"

    @property
    def share(self) -> Fraction | None:
        if self.share_numerator and self.share_denominator:
            return Fraction(self.share_numerator, self.share_denominator)
        return None

    def __str__(self) -> str:
        return self.label
