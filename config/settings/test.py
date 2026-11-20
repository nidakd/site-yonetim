from .base import *  # noqa: F403

SECRET_KEY = "test-anahtari"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # testler hızlı çalışsın
SMS_BACKEND = "apps.comms.sms.LocmemBackend"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
