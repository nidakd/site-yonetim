# Site Yönetim

Siteler, apartmanlar ve yönetim firmaları için aidat, tahsilat ve muhasebe platformu.
Çok kiracılı (multi-tenant) SaaS olarak tasarlandı: her müşteri kuruluşun verisi hem uygulama
hem veritabanı seviyesinde diğerlerinden ayrıdır.

> Durum: **Faz 2 (kurulum ve sakin portalı) tamamlandı**. Ürün planı, faz belgeleri ve tasarım
> notları `docs/` klasöründe yerel olarak tutulur; depoya dahil değildir.

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

Denemek için tamamen sentetik verili bir demo sitesi de kurulabilir (iki blok, 16 bölüm,
iki aylık aidat, tahsilat ve giderler):

```bash
python manage.py seed_demo --org-name "Demo" --admin-email demo@example.test
```

Gerçek bir siteyi kurmak için panelde site → **Kurulum** sekmesinden şablonu indirip doldurun
(ya da eski aylık aidat çizelgenizi yükleyin); önizlemede kontrol edip onaylayın.

Sakinler `/sakin/giris/` adresinden yönetimde kayıtlı cep telefonlarıyla SMS koduyla girer.
Geliştirmede SMS gönderilmez, kod `runserver` çıktısına yazılır (`SMS_BACKEND`).

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
denetim izinin değiştirilemezliği; para dağıtımında kuruşun kaybolmaması (hypothesis), defterin
dengesi ve değiştirilemezliği, dönem kilidi, tahakkuk/tahsilat/avans/iptal senaryolarından sonra
her bölümde belgeler ile defterin aynı bakiyeyi vermesi.

## Mimari

```
config/            ayarlar (base / dev / test / prod), URL'ler
apps/core/         istek bağlamı (kiracı, kullanıcı, IP), platform yönetim paneli
apps/tenancy/      kuruluş, site, üyelik, roller/yetkiler, RLS yardımcıları, kiracı ara katmanı
apps/accounts/     kullanıcı, giriş, iki adımlı doğrulama, yedek kodlar, şifre sıfırlama
apps/audit/        değiştirilemez denetim izi
apps/property/     blok, bağımsız bölüm (arsa payı, m², tip)
apps/people/       kişi, tarihli malik/kiracı ilişkisi
apps/ledger/       hesap planı, değiştirilemez çift taraflı defter, dönem kilidi, mizan
apps/billing/      borç türü, özel aidat, tahakkuk sihirbazı (önizleme → onay → iptal)
apps/payments/     tahsilat, eşleştirme, avans, makbuz
apps/expenses/     gider, tedarikçi, kasa/banka hesabı, virman
apps/reports/      site özeti, bölüm ekstresi, borçlular
apps/imports/      Excel'den kurulum: şablon, aylık aidat çizelgesi, önizleme → onay
apps/residents/    sakin girişi (telefon + SMS kodu), sakin portalı, portal gizlilik ayarları
apps/comms/        SMS arayüzü, e-posta bildirimleri (makbuz, ekstre) ve kayıtları
domain/            Django'dan bağımsız saf fonksiyonlar (kuruş, dağıtım, eşleştirme, telefon, Excel hücresi)
templates/         arayüz (Tailwind bileşen sınıfları: static/src/app.css)
tests/
```

**Kiracı izolasyonu iki kilitlidir:**
1. Uygulama: kiracı modelleri (`TenantModel`) varsayılan olarak yalnızca istekteki kuruluşun
   kayıtlarını döndürür; bağlam yoksa hata verir (fail-closed).
2. Veritabanı: her kiracı tablosunda PostgreSQL RLS politikası. Kod tarafında bir filtre
   unutulsa ya da doğrudan SQL yazılsa bile başka kuruluşun satırı dönmez, yazılamaz.

**Denetim izi** veritabanı tetikleyicisiyle korunur: kayıtlar güncellenemez ve silinemez.

**Defter** de veritabanında korunur: yevmiye kayıtları değiştirilemez ve silinemez, her kaydın
borç/alacak toplamı işlem sonunda denetlenir, kilitli döneme kayıt girilemez. Ekranlardaki her
rakam (bakiye, ekstre, kasa) defterden okunur; görünümler yalnızca servis çağırır, para hesabı
`domain/` altındadır.
