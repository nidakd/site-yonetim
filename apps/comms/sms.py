"""SMS gönderimi: sağlayıcı bir arayüzün arkasında (Netgsm, İleti Merkezi vb. sonra eklenir).

`SMS_BACKEND` ayarı hangi sınıfın kullanılacağını söyler:
- `ConsoleBackend`: geliştirmede mesajı sunucu günlüğüne yazar (giriş kodu orada görünür).
- `LocmemBackend`: testlerde `outbox` listesine koyar.
- `DisabledBackend`: canlıda sağlayıcı ayarlanmamışsa gönderim hata verir (kod sessizce kaybolmaz).

Ürün içinden yalnızca bilgilendirme SMS'i gönderilir (giriş kodu); ticari ileti yoktur (Plan §3.2).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils.module_loading import import_string

from domain.phone import mask_phone

logger = logging.getLogger("sms")
outbox: list[dict] = []


class SmsError(RuntimeError):
    pass


class BaseBackend:
    def send(self, phone: str, text: str) -> None:
        raise NotImplementedError


class ConsoleBackend(BaseBackend):
    def send(self, phone: str, text: str) -> None:
        logger.warning("SMS (geliştirme) → %s: %s", mask_phone(phone), text)


class LocmemBackend(BaseBackend):
    def send(self, phone: str, text: str) -> None:
        outbox.append({"phone": phone, "text": text})


class DisabledBackend(BaseBackend):
    def send(self, phone: str, text: str) -> None:
        raise SmsError("SMS sağlayıcısı ayarlanmamış (SMS_BACKEND).")


def send_sms(phone: str, text: str) -> None:
    backend = import_string(settings.SMS_BACKEND)()
    backend.send(phone, text)
