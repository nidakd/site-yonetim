# Faz 0: Temel altyapı

Durum: tamamlandı (19.11.2026) · Plan: [URUN_PLANI.md](URUN_PLANI.md) §11

## Kabul kriteri
- [x] Boş bir kuruluş ve site açılabiliyor, yönetici giriş yapabiliyor
- [x] İki kuruluş birbirinin verisini göremiyor (ORM, doğrudan SQL, URL manipülasyonu; testli)

## Yapılanlar
| Konu | Karar | Neden |
| --- | --- | --- |
| Çatı | Django 5.2 LTS, modüler monolit (`apps/*`) | Hazır kimlik/yetki/migration; uzun süreli destek |
| Kiracı modeli | Tek veritabanı, `organization_id` + `TenantModel` yöneticisi + PostgreSQL RLS (FORCE) | Şema-başına-kiracıya göre basit; RLS ikinci kilit |
| DB rolü | Uygulama süper kullanıcı olmayan rolle bağlanır | Süper kullanıcı RLS'i atlar; test bunu denetler |
| Platform erişimi | `platform_context()` yalnızca yönetim komutları ve iki adımlı doğrulamalı platform panelinde | Destek işleri izolasyonu bilinçli ve görünür şekilde kaldırır |
| Kullanıcı | Özel `User` (e-posta ile giriş, ileride telefon) | Kullanıcı modeli sonradan değiştirilemez; baştan doğru kuruldu |
| Şifre | Argon2, en az 10 karakter, yaygın/benzer şifre engeli | Güncel öneri |
| 2FA | TOTP zorunlu + 10 tek kullanımlık yedek kod | Muhasebe ve kişisel veriye erişen hesaplar |
| Cihazı hatırla | İsteğe bağlı, 30 gün; çerezde rastgele değer, veritabanında yalnızca SHA-256 özeti; şifre değişince ve Güvenlik sayfasından iptal | Her girişte kod sormak yöneticiyi yorar; şifre yine zorunlu |
| Kilitleme | E-posta başına 5, IP başına 30 hatalı deneme / 15 dk; 2FA'da 5 hatalı kod girişi bitirir | Kaba kuvvet koruması |
| Mesajlar | Genel hata metinleri; şifre sıfırlama kayıtlı olmayan e-postayı belli etmez | Kullanıcı varlığı sızmaz |
| Roller | Kuruluş yöneticisi, site yöneticisi, muhasebe, denetçi, saha görevlisi; kuruluş geneli ya da siteye özel | Plan §4 |
| Denetim izi | Ayrı tablo, RLS'li, UPDATE/DELETE/TRUNCATE tetikleyiciyle yasak, işlemle aynı transaction | Değiştirilemez kanıt |
| Arayüz | Django şablonları + HTMX + Alpine + Tailwind 4; mobil öncelikli; harici CDN yok | Tek kod tabanı; KVKK için üçüncü taraf istek yok |
| CI | ruff, eksik migration kontrolü, pytest (süper kullanıcı olmayan rolle), `check --deploy` | Her değişiklik aynı kapıdan geçer |

## Bilinçli olarak sonraya bırakılanlar
- **Staging sunucusu ve Docker:** barındırma kararı bekleniyor (Plan §13). Docker bu makinede kurulu
  olmadığı için denenmemiş dosya eklenmedi.
- **Ekipten kullanıcı davet etme ekranı:** şimdilik platform panelinden ya da komutla; Faz 1'de panelden.
- **E-posta gönderimi:** geliştirmede konsola yazılır; canlıda SMTP ayarı ile.
- **Redis:** önbellek (giriş sayaçları) geliştirmede bellekte; canlıda `REDIS_URL` ile Redis.
  Birden çok süreçte çalışırken Redis zorunlu, aksi halde kilitleme süreç başına sayılır.
- **Sakin girişi (telefon):** Faz 2.

## Sonraki adım: Faz 1 (çekirdek muhasebe)
Blok/bağımsız bölüm/arsa payı, kişiler ve tarihli malik-kiracı ilişkisi, `domain/` para-dağıtım-
eşleştirme fonksiyonları, çift taraflı defter, tahakkuk sihirbazı, tahsilat + makbuz, gider/kasa/banka,
bölüm ekstresi.
