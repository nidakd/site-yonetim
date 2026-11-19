from datetime import date

from django.db import models
from django.db.models import Q

from apps.property.models import Unit
from apps.tenancy.models import TenantModel


class Person(TenantModel):
    """Kuruluş kapsamında kişi: bir kişi birden çok bölümde malik/kiracı olabilir.

    TCKN bilinçli olarak yok (veri minimizasyonu, Plan §3.2). Kişi ilişkisi bitince
    "anonimleştir" ile iletişim bilgisi silinir; muhasebe kayıtları bozulmaz.
    """

    first_name = models.CharField("ad", max_length=80)
    last_name = models.CharField("soyad", max_length=80)
    phone = models.CharField("telefon", max_length=20, blank=True)
    email = models.EmailField("e-posta", blank=True)
    notes = models.TextField("not", blank=True)
    is_anonymized = models.BooleanField(default=False, editable=False)

    class Meta:
        verbose_name = "kişi"
        verbose_name_plural = "kişiler"
        ordering = ["first_name", "last_name"]
        indexes = [models.Index(fields=["organization", "phone"])]

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self) -> str:
        return self.full_name


class Occupancy(TenantModel):
    """Kişi ↔ bölüm ilişkisi; tarihli. Malik satışı, kiracı çıkışı bitiş tarihiyle kapanır."""

    class Relation(models.TextChoices):
        OWNER = "owner", "Malik"
        TENANT = "tenant", "Kiracı"
        RESIDENT = "resident", "Oturan"

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="occupancies")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="occupancies")
    relation = models.CharField("ilişki", max_length=20, choices=Relation.choices)
    share_percent = models.DecimalField(
        "hisse (%)", max_digits=5, decimal_places=2, null=True, blank=True, help_text="Hisseli maliklerde"
    )
    start_date = models.DateField("başlangıç")
    end_date = models.DateField("bitiş", null=True, blank=True)
    is_liable = models.BooleanField("borç sorumlusu", default=True)
    receives_notifications = models.BooleanField("bildirim alır", default=True)

    class Meta:
        verbose_name = "oturum/mülkiyet"
        verbose_name_plural = "oturum/mülkiyet kayıtları"
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")), name="occupancy_dates"
            ),
            models.CheckConstraint(
                condition=Q(share_percent__isnull=True) | Q(share_percent__gt=0, share_percent__lte=100),
                name="occupancy_share_percent",
            ),
        ]

    def is_active_on(self, day: date) -> bool:
        return self.start_date <= day and (self.end_date is None or self.end_date >= day)

    def __str__(self) -> str:
        return f"{self.person} · {self.unit} · {self.get_relation_display()}"


def active_on(day: date) -> Q:
    """Verilen günde geçerli ilişkiler için sorgu koşulu."""
    return Q(start_date__lte=day) & (Q(end_date__isnull=True) | Q(end_date__gte=day))
