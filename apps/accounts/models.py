from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra):
        if not email:
            raise ValueError("E-posta zorunlu")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Panel kullanıcısı (yönetici, muhasebe, denetçi...). Sakin girişi Faz 2'de telefonla eklenecek;
    bu yüzden `phone` alanı şimdiden var ve e-posta yerine kimlik olabilecek şekilde tasarlandı."""

    email = models.EmailField("e-posta", unique=True)
    phone = models.CharField(
        "cep telefonu", max_length=10, unique=True, null=True, blank=True, help_text="5XXXXXXXXX biçiminde"
    )
    full_name = models.CharField("ad soyad", max_length=150)
    is_active = models.BooleanField("aktif", default=True)
    is_staff = models.BooleanField(
        "platform personeli", default=False, help_text="Platform yönetim paneline girebilir (müşteri verisine değil)."
    )
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "kullanıcı"
        verbose_name_plural = "kullanıcılar"

    def __str__(self) -> str:
        return self.full_name or self.email


class TrustedDevice(models.Model):
    """ "Bu cihazı hatırla": şifre yine sorulur, yalnızca doğrulama kodu atlanır.

    Tarayıcıdaki çerezde `<id>:<gizli değer>` durur; veritabanında gizli değerin yalnızca SHA-256
    özeti saklanır (veritabanı sızsa bile çerez üretilemez). Şifre değişince ya da kullanıcı
    Güvenlik sayfasından isterse tüm kayıtlar iptal edilir.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="trusted_devices")
    token_hash = models.CharField(max_length=64)
    label = models.CharField("cihaz", max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "güvenilen cihaz"
        verbose_name_plural = "güvenilen cihazlar"

    def __str__(self) -> str:
        return f"{self.user} · {self.label}"
