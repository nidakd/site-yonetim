from django.conf import settings
from django.db import models

from apps.core.context import current_org_id, require_org_id


class Organization(models.Model):
    """Faturalanan müşteri: tek bir site yönetimi ya da birden çok site yöneten firma."""

    class Kind(models.TextChoices):
        SINGLE_SITE = "single_site", "Site yönetimi"
        MANAGEMENT_COMPANY = "management_company", "Yönetim firması"

    name = models.CharField("ad", max_length=200)
    kind = models.CharField("tür", max_length=30, choices=Kind.choices, default=Kind.SINGLE_SITE)
    is_active = models.BooleanField("aktif", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "kuruluş"
        verbose_name_plural = "kuruluşlar"

    def __str__(self) -> str:
        return self.name


class TenantQuerySet(models.QuerySet):
    pass


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Varsayılan yönetici: yalnızca bağlamdaki kuruluşun kayıtları. Bağlam yoksa hata verir."""

    def get_queryset(self):
        org_id = require_org_id()
        qs = super().get_queryset()
        return qs if org_id is None else qs.filter(organization_id=org_id)


class TenantModel(models.Model):
    """Kiracıya ait tüm modellerin tabanı. Alt sınıfın tablosunda RLS açılmalıdır (bkz. rls.py)."""

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, editable=False, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantManager()
    unscoped = models.Manager()  # yalnızca platform işleri ve migration'lar için

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        org_id = current_org_id()
        if self.organization_id is None:
            self.organization_id = require_org_id()
        elif org_id is not None and self.organization_id != org_id:
            raise PermissionError("Kayıt, bağlamdaki kuruluşa ait değil.")
        super().save(*args, **kwargs)


class Site(TenantModel):
    name = models.CharField("site adı", max_length=200)
    address = models.TextField("adres", blank=True)
    city = models.CharField("il", max_length=60, blank=True)
    district = models.CharField("ilçe", max_length=60, blank=True)
    is_active = models.BooleanField("aktif", default=True)

    class Meta:
        verbose_name = "site"
        verbose_name_plural = "siteler"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="site_unique_name_per_org")]

    def __str__(self) -> str:
        return self.name


class Role(models.TextChoices):
    ORG_ADMIN = "org_admin", "Kuruluş yöneticisi"
    SITE_ADMIN = "site_admin", "Site yöneticisi"
    ACCOUNTANT = "accountant", "Muhasebe"
    AUDITOR = "auditor", "Denetçi"
    FIELD_STAFF = "field_staff", "Saha görevlisi"


class Membership(models.Model):
    """Kullanıcının bir kuruluştaki (ve isteğe bağlı olarak tek bir sitedeki) rolü.

    Bilinçli olarak RLS dışında: giriş sırasında kullanıcının hangi kuruluşlara üye olduğu,
    kuruluş seçilmeden önce okunur. Bu tabloya her zaman `user` filtresiyle erişilir.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
        help_text="Boşsa kuruluşun tüm sitelerinde geçerli",
    )
    role = models.CharField("rol", max_length=30, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "üyelik"
        verbose_name_plural = "üyelikler"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization", "site", "role"], name="membership_unique", nulls_distinct=False
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} · {self.organization} · {self.get_role_display()}"
