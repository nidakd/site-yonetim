# Site Yönetim

Siteler, apartmanlar ve yönetim firmaları için aidat, tahsilat ve muhasebe platformu.
Çok kiracılı (multi-tenant) SaaS olarak tasarlandı: her müşteri kuruluşun verisi hem uygulama
hem veritabanı seviyesinde diğerlerinden ayrıdır.

> Durum: **Faz 0 (temel altyapı)**. Yol haritası ve tasarım kararları: [docs/URUN_PLANI.md](docs/URUN_PLANI.md),
> bu fazın kapsamı: [docs/faz-0.md](docs/faz-0.md).

## Teknoloji
Django 5.2 LTS · PostgreSQL 16 (satır seviyesi güvenlik) · HTMX + Alpine.js · Tailwind CSS 4 ·
django-otp (iki adımlı doğrulama) · pytest

## Yerel kurulum

Gerekenler: Python 3.12+, PostgreSQL 14+, Node.js (yalnızca CSS'i yeniden derlemek için).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Uygulama veritabanına **süper kullanıcı olmayan** bir rolle bağlanmalı; süper kullanıcılar
satır seviyesi güvenliği (RLS) atlar ve kiracı izolasyonunun ikinci kilidi çalışmaz:

```bash
psql -d postgres -c "CREATE ROLE site_yonetim_app LOGIN CREATEDB NOSUPERUSER NOBYPASSRLS"
createdb -O site_yonetim_app site_yonetim
python manage.py migrate
```

İlk müşteri kuruluşunu ve yöneticisini oluşturun (komut, şifre belirleme bağlantısı yazar):

```bash
python manage.py create_organization --name "Örnek Site Yönetimi" --admin-email yonetici@example.com --admin-name "Ad Soyad"
python manage.py runserver
```

Bağlantıyla şifre belirlenir, ilk girişte iki adımlı doğrulama kurulur (Google/Microsoft
Authenticator vb.), sonra panel açılır. "Bu cihazı 30 gün hatırla" seçilirse o tarayıcıdan
yalnızca şifre sorulur; hatırlanan cihazlar Güvenlik sayfasından silinebilir.

CSS ya da şablonlarda Tailwind sınıfı değiştiğinde:

```bash
npm install
npm run build
```

## Testler

```bash
pytest -q
ruff check . && ruff format --check .
```

Testler gerçek PostgreSQL üzerinde çalışır ve şunları güvenceye alır: iki kuruluşun birbirinin
verisini ne ORM'le ne doğrudan SQL'le görememesi, her kiracı tablosunda RLS'in açık olması
(yeni tablo eklenip RLS unutulursa test kırılır), giriş/2FA/kilitleme akışı, rol yetkileri ve
denetim izinin değiştirilemezliği.

## Mimari

```
config/            ayarlar (base / dev / test / prod), URL'ler
apps/core/         istek bağlamı (kiracı, kullanıcı, IP), platform yönetim paneli
apps/tenancy/      kuruluş, site, üyelik, roller/yetkiler, RLS yardımcıları, kiracı ara katmanı
apps/accounts/     kullanıcı, giriş, iki adımlı doğrulama, yedek kodlar, şifre sıfırlama
apps/audit/        değiştirilemez denetim izi
templates/         arayüz (Tailwind bileşen sınıfları: static/src/app.css)
tests/
```

**Kiracı izolasyonu iki kilitlidir:**
1. Uygulama: kiracı modelleri (`TenantModel`) varsayılan olarak yalnızca istekteki kuruluşun
   kayıtlarını döndürür; bağlam yoksa hata verir (fail-closed).
2. Veritabanı: her kiracı tablosunda PostgreSQL RLS politikası. Kod tarafında bir filtre
   unutulsa ya da doğrudan SQL yazılsa bile başka kuruluşun satırı dönmez, yazılamaz.

**Denetim izi** veritabanı tetikleyicisiyle korunur: kayıtlar güncellenemez ve silinemez.
