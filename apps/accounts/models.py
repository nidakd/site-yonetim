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
