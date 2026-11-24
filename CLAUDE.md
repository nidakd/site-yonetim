# Site Yönetim – çalışma notları

**Yeni bir oturumda önce `docs/DEVAM.md` dosyasını oku** (yerel, depoda yok): güncel durum, yapılacaklar
ve açık sorular orada. Ürün planı `docs/URUN_PLANI.md`, faz kararları `docs/faz-*.md`.

## Kurallar
- Kullanıcıyla Türkçe, sade konuş; büyük işlerden önce kısa plan sun ve onay al.
- Her mantıksal adım ayrı commit + `git push`. Commit/PR metninde Claude/Anthropic adı ya da
  Co-Authored-By satırı **olmaz**.
- "Bitti" = `pytest -q` ve `ruff check . && ruff format --check .` temiz, GitHub CI yeşil, ekranlar
  tarayıcıda denendi.
- Her yeni kiracı tablosu `TenantModel` (apps/tenancy/models.py) + migration'da `enable_rls(...)`;
  `tests/test_rls.py` bunu denetler. RLS dışı tablo ancak gerekçeyle (bkz. residents/models.py).
- Para her zaman kuruş (`int`), `float` yok; para hesabı `domain/` altında ve testli.
- Defter kaydı silinmez/değiştirilmez; düzeltme ters kayıtla (`apps/ledger/services.py`).
- Gerçek kişi verisi (ad, telefon, Excel dosyaları) depoya, testlere, belgelere girmez (KVKK);
  testlerde yalnızca sentetik veri.

## Komutlar
```bash
.venv/bin/python manage.py runserver 127.0.0.1:8001
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python manage.py makemigrations --check --dry-run
npm run build:css   # şablonlarda yeni Tailwind sınıfı kullanınca
```
Geliştirmede sakin SMS kodu sunucu çıktısına yazılır (`SMS_BACKEND` konsol).

## Yapı
`apps/` modülleri: tenancy (kuruluş/site/üyelik/yetki), accounts (giriş, 2FA), audit, property
(blok/daire), people (kişi, tarihli ilişki), ledger (defter, mizan, kilit), billing (tahakkuk, açılış,
geçmiş aktarım), payments (tahsilat, eşleşme, avans, makbuz), expenses, reports, imports (Excel:
şablon, aylık çizelge, tarihli aidat/yakıt çizelgesi), residents (sakin portalı), comms (SMS, e-posta).
Görünümler yalnızca servis çağırır; site menüsü `apps/core/templatetags/ui.py` (`SITE_MENU`).
