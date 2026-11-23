from django.conf import settings
from django.db import models

from apps.tenancy.models import Site, TenantModel


class ImportJob(TenantModel):
    """Bir Excel yüklemesi: çözümlenir → önizlenir → onaylanınca uygulanır.

    Çözümlenen satırlar (`payload`) yalnızca önizleme süresince tutulur; uygulandıktan ya da
    vazgeçildikten sonra silinir, yalnızca özet ve bulgular kalır (veri minimizasyonu)."""

    class Kind(models.TextChoices):
        SETUP = "setup", "Kurulum şablonu"
        GRID = "grid", "Aylık aidat çizelgesi"
        LEDGER = "ledger", "Tarihli aidat/yakıt çizelgesi"

    class Status(models.TextChoices):
        PREVIEW = "preview", "Önizleme"
        APPLIED = "applied", "Uygulandı"
        DISCARDED = "discarded", "Vazgeçildi"

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="import_jobs")
    kind = models.CharField("tür", max_length=20, choices=Kind.choices)
    status = models.CharField("durum", max_length=20, choices=Status.choices, default=Status.PREVIEW)
    file_name = models.CharField("dosya", max_length=200)
    file_sha256 = models.CharField(max_length=64)
    options = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    issues = models.JSONField(default=list, blank=True)  # [{level, where, message}]
    summary = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "içe aktarma"
        verbose_name_plural = "içe aktarmalar"
        ordering = ["-created_at", "-id"]

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.get("level") == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.get("level") == "warning")

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.file_name}"
