"""Sakin kimliği: telefon numarası (SMS koduyla kanıtlanır).

`ResidentAccount` ve `OtpChallenge` bilinçli olarak RLS dışında: sakin giriş yaparken hangi
kuruluşa ait olduğu henüz bilinmez (yönetici `User`/`Membership` tablolarıyla aynı gerekçe).
Bu tablolara her zaman telefon filtresiyle erişilir; kişi adı ya da bölüm bilgisi içermezler.
Sakinin hangi bölümleri göreceği her istekte kiracı bağlamında, yöneticinin kaydından hesaplanır.
"""

from django.contrib.auth.hashers import check_password, make_password
from django.db import models

from apps.tenancy.models import Site, TenantModel


class ResidentAccount(models.Model):
    phone = models.CharField("telefon", max_length=20, unique=True)
    password = models.CharField(max_length=128, blank=True)  # isteğe bağlı şifre (hash)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "sakin hesabı"
        verbose_name_plural = "sakin hesapları"

    def set_password(self, raw: str) -> None:
        self.password = make_password(raw)

    def check_password(self, raw: str) -> bool:
        return bool(self.password) and check_password(raw, self.password)

    @property
    def has_password(self) -> bool:
        return bool(self.password)

    def __str__(self) -> str:
        return f"Sakin {self.pk}"


class OtpChallenge(models.Model):
    """Tek kullanımlık SMS giriş kodu. Kod düz yazılmaz (HMAC özeti)."""

    phone = models.CharField(max_length=20, db_index=True)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Kod {self.pk}"


class ResidentConsent(TenantModel):
    """Aydınlatma metninin okunduğu kaydı: veri sorumlusu (kuruluş) başına, sürümüyle."""

    account = models.ForeignKey(ResidentAccount, on_delete=models.CASCADE, related_name="+")
    version = models.CharField(max_length=20)
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "account", "version"], name="consent_unique_version")
        ]


class PortalSettings(TenantModel):
    """Sakin portalında ne gösterileceği (site başına). Anonim özet seçenekleri varsayılan kapalı;
    hiçbir seçenek başka bir bölümün borcunu ya da kişinin adını göstermez (Plan §3.2)."""

    site = models.OneToOneField(Site, on_delete=models.CASCADE, related_name="portal_settings")
    portal_enabled = models.BooleanField(
        "sakin portalı açık", default=True, help_text="Kapalıysa sakinler bu sitenin bölümlerini göremez."
    )
    show_collection_rate = models.BooleanField(
        "bu ayın tahsilat oranı", default=False, help_text="Tahakkuk edilenin yüzde kaçının ödendiği."
    )
    show_cash_balance = models.BooleanField("kasa ve banka toplamı", default=False)
    show_expenses = models.BooleanField(
        "giderler (kategori toplamları)", default=False, help_text="Bu yılın giderleri kalem kalem toplam olarak."
    )
    contact_note = models.CharField(
        "iletişim notu",
        max_length=255,
        blank=True,
        help_text="Sakinlere gösterilir; ör. 'Yönetim ofisi hafta içi 10–17, 0212 000 00 00'.",
    )

    class Meta:
        verbose_name = "portal ayarı"
        verbose_name_plural = "portal ayarları"
