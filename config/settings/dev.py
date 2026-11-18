from .base import *  # noqa: F403
from .base import env

DEBUG = True
SECRET_KEY = env("SECRET_KEY", "yalnizca-yerel-gelistirme-icin-guvensiz-anahtar")
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
