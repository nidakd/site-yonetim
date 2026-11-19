# Site Yönetim Sistemi: Ürün ve Mimari Planı

Tarih: 19.11.2026 · Durum: **teknoloji kararı verildi (Django); diğer kararlar bekliyor** (§13)

Amaç: Bugünkü tek-site Streamlit uygulamasını, başka sitelere satılabilecek, birden çok siteyi
ve yönetim firmasını aynı anda taşıyabilen, üzerinde yıllarca geliştirme yapılabilecek gerçek
bir ürüne dönüştürmek.

---

## 1. Neden kökten değişim

Bugünkü uygulama işini görüyor ve case study için değerli (`v1.0-baslangic` etiketi ve son
hali korunuyor). Ama ürün olarak satılmasının önünde yapısal engeller var; bunlar yamayla
çözülmez:

| Konu | Bugün | Ürün için gereken |
| --- | --- | --- |
| Arayüz teknolojisi | Streamlit: her tıklamada tüm sayfa betiği yeniden çalışır; oturum, yetki, URL, mobil deneyim sınırlı | Gerçek web uygulaması: hızlı sayfalar, mobil uyumlu sakin ekranı, ileride mobil uygulama için API |
| Çok kiracılık | `complex_id` var ama her sorguya elle eklenmiş, unutulan yerler oldu (rehber §2.4) | Kiracı izolasyonu çerçeve ve veritabanı seviyesinde zorunlu (unutulamaz) |
| Muhasebe modeli | Borç + ödeme + eşleşme; kasa ayrı tabloda, ikisi senkron tutulmaya çalışılıyor | Çift taraflı defter: her olay dengeli kayıt, düzeltme ters kayıtla, dönem kapanışı |
| Borç dağıtımı | Sabit tutar ya da tip bazlı | Kat Mülkiyeti Kanunu'na uygun dağıtım anahtarları: arsa payı, m², eşit, kişi sayısı, sayaç, özel anlaşma |
| Kişiler | `owner_name` metin alanı | Kişi ↔ bağımsız bölüm ilişkisi tarihli (malik/kiracı değişir, hisseli malik olur) |
| Tahsilat | Elle giriş | Banka ekstresi içe aktarma + otomatik eşleştirme, ileride online ödeme |
| İletişim | Yok | SMS / e-posta / bildirim, duyuru, talep-arıza |
| İş mantığının yeri | Kısmen SQL trigger, kısmen Python, kısmen ekran kodu | Tek yerde, saf ve test edilebilir alan (domain) katmanı |
| Satış altyapısı | Yok | Abonelik, deneme süresi, kurulum sihirbazı, Excel'den içe aktarma, yasal metinler |

**Karar önerisi:** Yeni bir depo (`site-yonetim`) açıp sıfırdan, ama mevcut bilgiyi (FIFO
kuralları, testlerdeki senaryolar, `aidat_takip` Excel parser'ı, gizlilik modeli) taşıyarak
kurmak. Eski depo portfolyo için "v1" olarak kalır.

---

## 2. Ürün konumlandırması

**Hedef müşteri (öneri):**
1. **Birincil:** 20–300 bağımsız bölümlü, gönüllü ya da yarı-profesyonel yöneticisi olan siteler
   ve apartmanlar. İhtiyaçları: Excel'den kurtulmak, sakinlere şeffaf olmak, az zaman harcamak.
2. **İkincil (ikinci yıl):** Birden çok site yöneten profesyonel yönetim firmaları. İhtiyaçları:
   toplu panel, personel yetkileri, firma adına raporlama.

Veri modeli ilk günden ikisini de taşır (bkz. §5.1); arayüz önce birinciye odaklanır.

**Pazar gerçeği:** Türkiye'de bu alanda yerleşik oyuncular var (Apsiyon en bilineni). Rekabet
"daha çok özellik" ile değil, şunlarla kazanılır:
- **10 dakikada kurulum:** mevcut Excel çizelgesini yükle, sistem blok/daire/bakiye çıkarsın
  (`aidat_takip` parser'ı tam bu iş için yazıldı).
- **Sakin için şeffaflık, gizlilik bozulmadan:** yönetimin ayarlayabildiği anonim site özeti.
- **Gönüllü yöneticiye sadelik:** muhasebe bilmeden doğru kayıt; arka planda çift taraflı defter.
- **Uygun ve anlaşılır fiyat:** bağımsız bölüm başına aylık ücret (rakip fiyatlarını
  araştırıp belirleyelim).

**Pilot:** Sizin yönettiğiniz site ilk müşteri olur. 1–2 ay Excel ile paralel çalıştırıp
rakamların birebir tuttuğunu kanıtlarız; bu aynı zamanda case study'nin "sonuç" bölümüdür.

---

## 3. Alan bilgisi (domain) ve yasal çerçeve

> Bu bölüm ürün kararlarını yönlendirir; satışa çıkmadan önce bir avukat ve mali müşavire
> teyit ettirilmeli. Aşağıdakiler genel bilgidir, hukuki görüş değildir.

### 3.1 Kat Mülkiyeti Kanunu (634) ile ilgili noktalar
- **Bağımsız bölüm** (daire, dükkan, ofis, depo, otopark) temel birimdir; her birinin
  **arsa payı** vardır. Ortak giderler kural olarak arsa payına göre paylaştırılır; **yönetim
  planı** farklı bir esas koyabilir (eşit, m², kişi sayısı vb.). → Sistem birden çok dağıtım
  anahtarını desteklemeli; site bazında varsayılan, gider/borç bazında seçilebilir.
- **İşletme projesi:** yönetici yıllık tahmini gider bütçesini hazırlar, her bölümün payına
  düşen avansı (aidat) belirler, genel kurul onaylar. → Bütçe modülü aidatı üretmeli.
- **Gecikme tazminatı:** kanunda ödemede gecikilen süre için aylık oranlı tazminat öngörülür
  (yönetim planı ve genel kurul kararı da belirleyici). → Kural motoru: oran, başlangıç günü,
  muafiyet, yuvarlama site bazında ayarlanabilir; varsayılan kapalı.
- **Kiracının sorumluluğu:** kiracı, kira süresince ortak giderlerden belirli koşullarla malikle
  birlikte sorumlu tutulabilir. → Her tahakkukta "sorumlu kişi(ler)" anlık görüntüsü tutulmalı.
- **Denetim hakkı:** kat malikleri ve denetçi hesapları inceleyebilir. → Salt-okunur
  "denetçi" rolü ve dönem raporları.
- **Genel kurul, karar defteri:** kararlar (aidat artışı, özel toplama, muafiyet) bir karara
  dayanır. → Özel aidat, muafiyet, toplu borçlandırma "karar referansı" alanı taşır.

### 3.2 KVKK
- **Rol dağılımı:** Site yönetimi (müşteri) **veri sorumlusu**, siz (yazılım sağlayıcı)
  **veri işleyen**. → Her müşteriyle **veri işleme sözleşmesi**, sizin için gizlilik politikası
  ve kullanım şartları; müşteriye sakinler için hazır **aydınlatma metni** şablonu.
- **Borçlu listesi ifşası:** borçlu daire/isim listesinin panoya asılması ya da toplu
  paylaşılması KVKK açısından risklidir; bu konuda Kurul kararları bulunuyor. → Ürün hiçbir
  ekranda başka kişinin borcunu sakine göstermez; anonim toplamlar yönetimin ayarıdır
  (bugünkü gizlilik modeli korunur).
- **Veri minimizasyonu:** TC kimlik no zorunlu değil; yalnızca icra takibi gerekirse,
  şifrelenmiş alanda, ayrı yetkiyle.
- **Barındırma yeri:** Türkiye'de barındırmak yurt dışına aktarım yükünü ortadan kaldırır.
  AB'de barındırma mümkün ama ek yükümlülükler doğurur (standart sözleşme vb.). → §13 karar.
- **Saklama ve silme:** muhasebe kayıtları yıllarca saklanmak zorunda olabilir; kişisel iletişim
  verisi ilişki bitince silinebilir. → Kişi kaydı "anonimleştir" işlemi muhasebe kaydını bozmaz.
- **SMS:** borç bildirimi gibi bilgilendirme mesajları ile ticari (pazarlama) mesajları ayrı
  tutulmalı; ticari ileti İYS kaydı ve onay gerektirir. → Ürün içinden pazarlama SMS'i yok.

### 3.3 Ticari ve vergisel (satış için)
- Şirket (şahıs ya da limited), aboneliklere e-fatura/e-arşiv, internetten satışta mesafeli
  satış sözleşmesi ve ön bilgilendirme. → Mali müşavirle başlangıçta netleştirilmeli.
- Site yönetiminin kendi tahsilatları için verilen **makbuz** ürünün işi; yönetim firmasının
  siteye kestiği hizmet faturası ürünün kapsamı dışında (ileride entegrasyon).

---

## 4. Kullanıcılar ve yetkiler

| Rol | Kapsam | Yapabilir | Yapamaz |
| --- | --- | --- | --- |
| **Platform yöneticisi** (siz) | Tüm sistem | Abonelik, destek modu (müşteri izniyle, loglanır) | Müşteri verisini izinsiz görmek |
| **Firma yöneticisi** | Bir yönetim firması, bağlı tüm siteler | Site açma, personel yetkilendirme, toplu raporlar | — |
| **Site yöneticisi** | Bir site | Her şey: yapı, kişiler, tahakkuk, tahsilat, gider, ayarlar | Kilitli dönemi değiştirmek (yalnızca açma yetkisiyle) |
| **Muhasebe** | Bir site | Tahakkuk, tahsilat, gider, rapor | Yapı ve yetki ayarları |
| **Denetçi** | Bir site | Tüm kayıt ve raporları görür | Hiçbir şey değiştiremez |
| **Saha görevlisi** | Bir site | Talep/arıza, nakit tahsilat girişi (onaya düşer) | Raporlar, kişi verisi |
| **Sakin** (malik/kiracı) | Kendi bölüm(ler)i | Kendi ekstresi, makbuzlar, ödeme, talep, duyuru, anket | Başka bölümün herhangi bir verisi |

- Bir kullanıcı birden çok sitede farklı rollere sahip olabilir (üyelik tablosu).
- Bir kişi birden çok bölümün maliki olabilir (iki daire, bir dükkan); tek girişle hepsini görür.
- Yönetici hesaplarında **iki adımlı doğrulama** (TOTP uygulaması) zorunlu. "Bu cihazı 30 gün
  hatırla" ile aynı tarayıcıdan kod ayda bir sorulur (şifre her zaman sorulur). Sonraki adım:
  passkey (Face ID / Touch ID) ile giriş; kod uygulaması yedek olarak kalır.

### 4.1 Sakin girişi (karar: 19.11.2026)
**Telefon + SMS doğrulama kodu (OTP)**, isteğe bağlı şifre:
1. Yönetici, bölümün malik/kiracı kaydına telefon numarasını girer (Excel'den de gelir).
2. Sakin giriş ekranına numarasını yazar, SMS ile 6 haneli kod gelir.
3. Kod doğruysa, **o numaranın yönetici tarafından kayıtlı olduğu bölümleri** görür.

Bu, sizin önerdiğiniz "telefon ile giriş" fikrini güvenli hale getirir: telefonun sahibi SMS ile
kanıtlanır, bölümle ilişkisi yöneticinin kaydıyla. Davet koduna gerek kalmaz (kod dağıtma
derdi biter). Maliyet: her girişte bir SMS; "beni 30 gün hatırla" ve isteğe bağlı şifre ile
azaltılır. Telefonu olmayan/yaşlı sakin için yönetici "ekstreyi yazdır / WhatsApp'tan gönder"
kullanır. Değerlendirilen alternatif (davet kodu + şifre) SMS maliyeti olmasa da sakinler için
zor bulundu; seçilmedi.

---

## 5. Veri modeli

Tüm para tutarları **kuruş cinsinden tam sayı** (`bigint`) tutulur; yuvarlama hatası olmaz.
Tüm tarihler `Europe/Istanbul`. Her tabloda `organization_id` (kiracı anahtarı), `created_at`,
`created_by`. Muhasebe kayıtları **silinmez, düzeltilmez**: hata ters kayıtla düzeltilir.

### 5.1 Kiracılık ve yapı
| Tablo | Önemli alanlar | Not |
| --- | --- | --- |
| `organization` | ad, tür (tek site / yönetim firması), abonelik | Faturalanan müşteri |
| `site` | ad, adres, şehir, varsayılan dağıtım anahtarı, aidat vade günü, gecikme kuralı, gizlilik ayarları, dönem başlangıcı | Bir organizasyonda N site |
| `block` | ad, sıra | Ada/parsel bilgisi isteğe bağlı |
| `unit` (bağımsız bölüm) | blok, no, kat, tür (daire/dükkan/otopark/depo/ofis), brüt m², arsa payı (pay/payda), bölüm tipi (2+1…), durum (aktif/arşiv) | Silinmez, arşivlenir |
| `unit_type` | ad, varsayılan aidat | Basit sitelerde dağıtım anahtarı yerine |
| `unit_group` | ad, üye bölümler | Ör. "asansörlü bloklar", "bahçe kullananlar"; dağıtımda kapsam |

### 5.2 Kişiler
| Tablo | Önemli alanlar | Not |
| --- | --- | --- |
| `person` | ad, soyad, telefon(lar), e-posta, iletişim tercihi, notlar; TCKN (opsiyonel, şifreli) | Organizasyon kapsamında; bir kişi birden çok bölümde |
| `unit_occupancy` | bölüm, kişi, ilişki (malik / hissedar malik / kiracı / oturan), hisse oranı, başlangıç–bitiş, borç sorumlusu mu, bildirim alır mı | Tarihli: malik değişimi, kiracı çıkışı izlenir |
| `user` | telefon, e-posta, şifre hash'i (opsiyonel), 2FA, son giriş | Kimlik; kişi kaydına bağlanır |
| `membership` | kullanıcı, organizasyon/site, rol | Yetkiler buradan |

### 5.3 Muhasebe çekirdeği
| Tablo | Önemli alanlar | Not |
| --- | --- | --- |
| `ledger_account` | kod, ad, tür (varlık/kaynak/gelir/gider), üst hesap | Site başına hazır hesap planı şablonu; kasa ve her banka hesabı bir hesap |
| `journal_entry` | tarih, açıklama, kaynak türü + kimliği, ters kaydı (varsa), dönem | Değişmez |
| `journal_line` | hesap, bölüm (ops.), kişi (ops.), borç, alacak | Her kayıtta Σborç = Σalacak (veritabanı kısıtı) |
| `period_lock` | site, kilitli son tarih, kilitleyen | Kilitli döneme kayıt girilemez |

Her iş olayı otomatik kayıt üretir; kullanıcı yevmiye görmek zorunda değildir:

| Olay | Borç | Alacak |
| --- | --- | --- |
| Aidat tahakkuku | Sakin alacakları (bölüm) | Aidat gelirleri |
| Tahsilat (banka) | Banka | Sakin alacakları (bölüm) |
| Gider ödemesi | Gider kategorisi | Kasa/Banka |
| Avans (fazla ödeme) | Banka | Alınan avanslar (bölüm) → sonraki tahakkukta mahsup |
| Borç silme (terkin) | Şüpheli/silinen alacak gideri | Sakin alacakları |

### 5.4 Tahakkuk (borçlandırma)
| Tablo | Önemli alanlar | Not |
| --- | --- | --- |
| `charge_type` | ad (aidat, yakıt, su, özel toplama, gecikme, açılış bakiyesi…), gelir hesabı, öncelik | Site ayarlanabilir |
| `distribution_key` | yöntem (eşit / arsa payı / m² / kişi sayısı / bölüm tipi / sabit / sayaç / manuel), kapsam (tüm site / blok / grup / bölüm türü) | |
| `dues_agreement` (özel aidat) | bölüm, tür (sabit tutar / indirim oranı / muaf), geçerlilik başlangıç–bitiş, karar referansı, not | Bugünkü özel aidatın tarihli, kararlı hali |
| `charge_batch` | dönem, ücret türü, toplam tutar ya da birim tutar, anahtar, durum (taslak → önizleme → onaylı → iptal) | Toplu borçlandırma; iptal = ters kayıt |
| `charge` | bölüm, tür, dönem, vade, tutar, açıklama, sorumlu kişi(ler) görüntüsü, batch | Tek tek borç kalemi |
| `meter_reading` | bölüm, sayaç türü, dönem, ilk/son değer | Pay ölçer / su sayacı dağıtımı için |
| `budget` + `budget_line` | yıl, kalem, tutar, anahtar | İşletme projesi → aylık aidat önerisi, bütçe/gerçekleşen raporu |

**Dağıtım ve yuvarlama:** Toplam tutar paylara bölünürken **en büyük kalan yöntemi** kullanılır;
dağıtılan kuruşların toplamı her zaman girilen toplama eşittir (property-based testle güvence).

### 5.5 Tahsilat
| Tablo | Önemli alanlar | Not |
| --- | --- | --- |
| `payment` | bölüm, ödeyen kişi, tarih, tutar, kanal (nakit / havale-EFT / kart / online), kasa-banka hesabı, referans, dekont dosyası, makbuz no, durum (geçerli / iade / iptal) | |
| `allocation` | ödeme, borç kalemi, tutar | Ödemenin hangi borcu kapattığı |
| `bank_statement` + `bank_line` | banka hesabı, satır tarihi, tutar, açıklama, gönderen IBAN/ad, eşleşme durumu, önerilen bölüm, güven skoru | Ekstre içe aktarma ve eşleştirme kuyruğu |
| `payer_hint` | IBAN / gönderen adı → bölüm | Bir kez eşleşen gönderen sonraki ayda otomatik tanınır |
| `receipt_sequence` | site, yıl, son numara | Boşluksuz makbuz numarası |

**Eşleştirme kuralları (varsayılan, site ayarlanabilir):**
1. Vadesi en eski borç önce (FIFO).
2. Aynı vadede tür önceliği: ör. aidat → yakıt → diğer → gecikme tazminatı en son.
3. Ödeyen belirli bir borcu işaret ettiyse (ör. "yakıt ödemesi") o önce kapanır.
4. Artan tutar **avans** olur; yeni tahakkuk gelince otomatik mahsup edilir.
5. Ödeme iptali/iadesi: eşleşmeler ters kayıtla geri alınır, kalan borçlar yeniden açılır.

### 5.6 Gider, kasa, banka
`vendor` (tedarikçi), `expense` (kategori, tedarikçi, belge no, belge dosyası, ödeme hesabı,
"sakinlere yansıt" seçeneği → dağıtım anahtarıyla tahakkuk üretir), `transfer` (kasa ↔ banka
virman), `cash_count` (kasa sayımı, fark kaydı), `employee` + basit maaş kaydı.

### 5.7 İletişim ve operasyon
`announcement` (hedef: tüm site / blok / grup), `notification` (kanal, şablon, alıcı, durum,
maliyet), `message_template`, `ticket` (talep/arıza: kategori, öncelik, fotoğraf, atanan,
durum, sakin görünürlüğü), `poll` + `vote` (bölüm başına tek oy, arsa payı ağırlıklı seçeneği),
`document` (yönetim planı, karar defteri sayfası, faturalar; görünürlük: yönetim / sakinler),
`meeting` (genel kurul: tarih, gündem, katılım, kararlar → karar referansı).

### 5.8 Sistem
`audit_event` (kim, ne zaman, hangi kayıt, önce/sonra özeti; yalnızca ekleme yapılabilir,
kişisel veri düz yazılmaz), `file` (S3 uyumlu depoda, şifreli), `subscription` + `invoice`
(sizin müşterilerinize), `feature_flag`, `import_job` (Excel/ekstre içe aktarma geçmişi ve
hata raporu).

---

## 6. Modüller ve kapsam (MVP ne, sonra ne)

| Modül | MVP (pilot + ilk satış) | Sonra |
| --- | --- | --- |
| Kurulum sihirbazı | Site → blok → bölüm; Excel'den toplu içe aktarma (bölüm, kişi, telefon, açılış bakiyesi) ve hata raporu | Tapu/arsa payı listesi içe aktarma |
| Kişiler | Malik/kiracı, tarihli ilişki, telefon/e-posta | Hisseli malik oranları, aile üyeleri |
| Tahakkuk | Aylık aidat (tip / arsa payı / eşit / sabit), özel aidat anlaşması, tek seferlik özel toplama, yakıt (tutar listesi ya da sayaç), önizleme + onay + iptal | İşletme projesinden otomatik aidat, gecikme tazminatı motoru |
| Tahsilat | Elle giriş, FIFO + öncelik, avans, iptal/iade, numaralı makbuz (PDF) | Banka ekstresi eşleştirme (MVP sonrası ilk iş), online ödeme |
| Gider | Kategori, tedarikçi, belge eki, kasa/banka | Gideri sakinlere yansıtma, tekrarlayan gider |
| Raporlar | Bölüm ekstresi, borç yaşlandırma, aylık tahsilat oranı, gelir-gider, kasa/banka durumu, Excel/PDF indir | Genel kurul faaliyet raporu, bütçe/gerçekleşen, mizan, icra listesi |
| Sakin portalı | OTP girişi, bölümlerim, ekstre, makbuzlar, anonim site özeti (ayarlı) | Online ödeme, talep, anket, duyuru, belge |
| Bildirim | E-posta; SMS ile giriş kodu | Borç hatırlatma SMS'i, toplu duyuru, mobil bildirim |
| Yetki | Site yöneticisi, muhasebe, denetçi, sakin | Firma paneli, saha görevlisi |
| Denetim izi | Tüm değişiklikler | Değişiklik geri alma talebi akışı |
| SaaS | Organizasyon, deneme süresi, elle faturalama | Online abonelik ödemesi, plan limitleri, beyaz etiket |

---

## 7. Teknik mimari

### 7.1 Seçenekler
| | **A. Django + HTMX** (öneri) | B. Next.js (TypeScript) | C. FastAPI + React |
| --- | --- | --- | --- |
| Dil | Python (bildiğiniz dil) | TypeScript | Python + TypeScript |
| Hazır gelenler | Kimlik, oturum, yetki, admin paneli, migration, form doğrulama, Türkçe yerelleştirme | Az; çoğu kütüphane seçimi sizde | Az; her şey parça parça |
| Arayüz | Sunucuda üretilen sayfalar + HTMX ile dinamik parçalar; tek kod tabanı | Zengin SPA | Zengin SPA, iki ayrı proje |
| Muhasebe/iş mantığı | Python'da güçlü (Decimal, test araçları, mevcut kodunuz) | Mümkün ama ekosistem zayıf | Güçlü |
| Tek kişinin bakımı | En kolay | Orta | En zor (iki uygulama) |
| Mobil uygulama | API (Django Ninja) üzerinden ileride React Native/Expo | Aynı dil avantajı | API hazır |
| Risk | Çok etkileşimli ekranlarda HTMX sınırı; gerekirse o ekrana küçük bir JS bileşeni | Bakım yükü, sık değişen ekosistem | Kurulum ve entegrasyon yükü |

**Öneri: A.** Bu ürünün zor kısmı arayüz değil, doğru muhasebe ve çok kiracılı güvenlik.
Django bunun için en çok hazır parçayı verir, sizin Python bilginizi kullanır, tek kişinin
yıllarca bakabileceği bir yapıdır. Sakin ekranı mobil öncelikli ve PWA olur (telefona
"uygulama gibi" eklenir); gerçek mobil uygulama gerekirse aynı API'yi kullanır.

### 7.2 Bileşenler (öneri)
| Katman | Seçim | Neden |
| --- | --- | --- |
| Backend | Django 5.2 LTS, Python 3.13 | Uzun süreli destek |
| Veritabanı | PostgreSQL 16 | Mevcut bilgi, satır seviyesi güvenlik (RLS), JSONB |
| Kiracı izolasyonu | Tek veritabanı, her tabloda `organization_id` + ORM'de zorunlu filtre + PostgreSQL RLS (ikinci kilit) | Sorgu unutulsa bile başka müşterinin verisi dönmez |
| Arayüz | Django şablonları + HTMX + Tailwind CSS + Alpine.js | Tek kod tabanı, hızlı, mobil uyumlu |
| API | Django Ninja (OpenAPI) | Mobil uygulama ve entegrasyonlar için |
| Arka plan işleri | Celery + Redis (ya da başlangıçta django-q/RQ) | SMS, PDF, ekstre içe aktarma, gece hesapları |
| Dosya | S3 uyumlu depolama (yerelde MinIO) | Dekont, fatura, belge |
| PDF | WeasyPrint | Makbuz, ekstre, raporlar (HTML şablondan) |
| Excel | openpyxl + `aidat_takip` parser'ı | İçe/dışa aktarma |
| SMS | Türk sağlayıcı (Netgsm, İleti Merkezi vb.) arayüz arkasında | Sağlayıcı değiştirilebilir |
| Online ödeme (sonra) | iyzico / PayTR / Param arayüz arkasında | Komisyon ve entegrasyon karşılaştırılacak |
| Hata izleme | Sentry | Canlı hatalar |
| Test | pytest, pytest-django, hypothesis (para dağıtımı), Playwright (uçtan uca) | |
| Kod kalitesi | ruff, mypy (alan katmanında), pre-commit | |
| CI/CD | GitHub Actions: lint + test + migration kontrolü; staging'e otomatik, canlıya onayla | |
| Ortam | Docker Compose (yerel ve sunucu) | Aynı ortam her yerde |

### 7.3 Kod yapısı (modüler monolit)
```
site-yonetim/
  config/                  ayarlar (base / dev / staging / prod)
  apps/
    tenancy/               organization, site, membership, kiracı bağlamı, RLS
    accounts/              user, OTP, 2FA, oturum, yetki kontrolü
    property/              block, unit, unit_type, unit_group, arsa payı
    people/                person, unit_occupancy, iletişim tercihleri
    ledger/                hesap planı, journal, dönem kilidi        ← saf alan mantığı
    billing/               charge_type, dağıtım, özel aidat, toplu tahakkuk, bütçe
    collections/           payment, allocation, makbuz, banka ekstresi eşleştirme
    expenses/              gider, tedarikçi, kasa, virman
    reports/               ekstre, yaşlandırma, gelir-gider, PDF/Excel
    portal/                sakin ekranları
    comms/                 bildirim, şablon, SMS/e-posta sağlayıcıları
    imports/               Excel kurulum sihirbazı, hata raporu
    audit/                 denetim izi
    saas/                  abonelik, plan, deneme süresi
  domain/                  framework'ten bağımsız saf fonksiyonlar: money, distribution,
                           allocation, late_fee (en yoğun test edilen yer)
  templates/, static/
  tests/
```
**Kural:** Para hesaplayan her kod `domain/` altında, veritabanı ve Django'dan bağımsız, saf
fonksiyon. Görünümler (views) yalnızca servis çağırır; servisler transaction açar, domain
fonksiyonunu çağırır, kayıtları ve denetim izini aynı transaction'da yazar.

### 7.4 Güvenlik
- Kiracı izolasyonu: ORM katmanı + RLS; her sorgu testinde "başka organizasyonun verisi
  dönmüyor" kontrolü (bugünkü testlerdeki `other site` senaryolarının genelleşmiş hali).
- Kimlik: sakin için OTP (5 dakika geçerli, 5 deneme, numara başına hız sınırı), yönetici için
  şifre + 2FA; oturum çerezleri `HttpOnly`, `Secure`, `SameSite`.
- Yetki: her servis fonksiyonunda rol kontrolü; görünümde gizlemek yetki sayılmaz.
- Girdi: Django form doğrulama, CSRF, şablonlarda otomatik kaçış; Excel/CSV formül enjeksiyonu
  koruması (dışa aktarmada `=`, `+`, `-`, `@` ile başlayan metinler).
- Gizli bilgiler: ortam değişkenleri / sunucu gizli deposu; repoda asla.
- Kişisel veri: TCKN gibi alanlar uygulama seviyesinde şifreli; log'larda maskeleme.
- Yedek: günlük tam + sürekli WAL (zamana dönük kurtarma), ayrı lokasyonda; **ayda bir geri
  yükleme provası**.
- Destek erişimi: müşteri onayıyla, süreli, denetim izine yazılır.

### 7.5 Kalite hedefleri
| Konu | Hedef |
| --- | --- |
| Sayfa yanıtı | Yönetici ekranları < 500 ms, sakin ekranı mobilde < 2 sn |
| Ölçek | Tek sunucuda 500 site × 200 bölüm rahat çalışmalı |
| Kesinti | Aylık %99,5; planlı bakım gece |
| Veri kaybı | En fazla 15 dakika (RPO), geri dönüş 4 saat (RTO) |
| Erişilebilirlik | Büyük yazı, yüksek kontrast, klavye ile kullanım (yaşlı sakinler) |
| Dil/biçim | Türkçe; para `1.234,56 ₺`, tarih `19.11.2026` |

---

## 8. Ayrıntıda düşünülmesi gereken durumlar (kontrol listesi)

Her biri için ürün davranışı tanımlı olacak ve testle güvence altına alınacak:

**Yapı ve kişiler**
- Daire satıldı, eski malikin borcu var: borç bölümde kalır mı, eski malike mi yazılır? (site
  ayarı; varsayılan: bölümde kalır, ekstre devir notu gösterir)
- Ay ortasında malik/kiracı değişti: o ayın aidatı kime? (varsayılan: dönem başındaki sorumlu)
- Kiracı borçla taşındı. Hisseli malikler (%50/%50): bildirim kime, ekstre kime?
- Aynı kişi iki dairenin maliki; aynı telefon iki kişide (aile); telefonu olmayan sakin.
- Bölüm birleştirme/bölme (iki daire tek daire oldu): eski bölümler arşivlenir, bakiyeler devredilir.
- Bölüm silme yok, arşiv var; arşivli bölüme tahakkuk yapılamaz ama geçmişi görünür.

**Tahakkuk**
- Toplu borçlandırma aynı dönem için iki kez çalıştırılırsa: engellenir ya da yalnızca eksikler.
- Yanlış toplu borçlandırma: tek tuşla iptal (ters kayıt), iz kalır.
- Aidat artışı geçmişe dönük karar: geçmiş borçlar değişmez, fark için ek tahakkuk üretilir.
- Özel aidat anlaşmasının başlangıç/bitiş tarihi; muafiyetin süresi.
- Kuruş yuvarlaması: toplam her zaman tam tutar.
- Yakıt: sayaç okuması eksik bölüm (tahmini mi, ortalama mı, uyarı mı?).

**Tahsilat**
- Borçtan önce ödeme (avans), fazla ödeme, eksik ödeme.
- Yanlış bölüme girilmiş ödeme: iptal + doğru bölüme yeniden giriş (iki kayıt da izde).
- Banka ekstresinde aynı satır iki kez yüklendi (tekrar kontrolü: tarih + tutar + açıklama + bakiye).
- Geri dönen EFT / karşılıksız çek: ödeme iptali, eşleşmeler geri alınır.
- Görevlinin elden topladığı nakit: onay kuyruğu, kasa sayımı.
- Makbuz numarası boşluksuz ve tekrarsız; iptal edilen makbuz "iptal" damgasıyla kalır.

**Dönem ve raporlar**
- Ay kapanışı sonrası geçmiş tarihe kayıt: engel; düzeltme cari döneme ters kayıtla.
- Yıl sonu devri, açılış bakiyeleri (Excel'den yaşlandırmalı borç listesi).
- Muaf bölümler oranlarda nasıl sayılır (paydaya girmez).

**Hizmet ilişkisi**
- Site sistemden ayrılıyor: tüm verinin Excel/PDF dışa aktarımı, ardından silme/anonimleştirme.
- Yönetici değişti (genel kurul sonrası): yetki devri, eski yöneticinin erişimi kapanır.
- Aboneliği biten site: salt-okunur mod, veri silinmez (süre tanımlı).

---

## 9. Sakin deneyimi (mobil öncelikli)
1. Giriş: telefon → SMS kodu → (ilk girişte) aydınlatma metni onayı.
2. Ana ekran: bölüm kartları (iki dairesi varsa iki kart), her birinde kalan borç ve son ödeme.
3. Bölüm detayı: açık borçlar (kısmi ödeme çubuğu), ödeme geçmişi ve hangi ayları kapattığı,
   makbuz PDF, ekstre PDF.
4. Ödeme (sonraki faz): kartla öde ya da "havale bilgileri + ödeme kodu" (banka eşleştirmesi için).
5. Site durumu: yönetimin izin verdiği anonim özet.
6. Duyurular, talep aç (fotoğraf), anket (sonraki faz).
7. Bildirim tercihleri: SMS / e-posta / hiçbiri (yasal bildirimler hariç).

## 10. Yönetici deneyimi
- **Ana panel:** bu ayın tahsilat oranı, gecikenler (sayı + tutar), kasa/banka bakiyesi,
  bekleyen işler (eşleşmemiş banka satırları, onay bekleyen nakit, açık talepler).
- **Günlük iş akışı:** ekstre yükle → eşleşmeleri onayla → hatırlatma gönder.
- **Aylık iş akışı:** tahakkuk sihirbazı (önizleme → onay) → ay sonu raporu → dönem kilidi.
- **Yıllık iş akışı:** işletme projesi → genel kurul raporu → yeni dönem aidatları.
- Her listede arama, filtre, Excel'e aktar; her kayıtta "geçmişi göster" (denetim izi).

---

## 11. Yol haritası

Süreler tek geliştirici + Claude için kaba tahmindir; her faz bitiminde çalışan, test edilmiş,
staging'de denenebilir bir sürüm olur.

| Faz | İçerik | Bitti sayılması için |
| --- | --- | --- |
| **0. Temel** (1–2 hf) | Yeni depo, Django iskeleti, Docker/yerel ortam, PostgreSQL + RLS, organizasyon/site/üyelik, yönetici girişi + 2FA, denetim izi altyapısı, tasarım sistemi (Tailwind bileşenleri), CI, staging sunucusu | Boş bir site açılıp yönetici giriş yapabiliyor; iki organizasyon birbirini göremiyor (testli) |
| **1. Çekirdek muhasebe** (3–5 hf) | Yapı (blok/bölüm/arsa payı), kişiler ve tarihli ilişkiler, `domain/` para-dağıtım-eşleştirme fonksiyonları, defter, tahakkuk sihirbazı, tahsilat + makbuz PDF, gider/kasa/banka, bölüm ekstresi, temel raporlar | Pilot sitenin bir ayı baştan sona sistemde yürütülebiliyor; tüm toplamlar defterle tutarlı |
| **2. Kurulum ve sakin** (2–3 hf) | Excel kurulum sihirbazı (aidat_takip), açılış bakiyeleri, sakin portalı (OTP), gizlilik ayarları, e-posta bildirimi | Pilot site 1 saatte kuruluyor; sakinler kendi ekstresini görüyor |
| **Pilot** (4–8 hf, paralel) | Kendi sitenizde Excel ile paralel çalıştırma, fark raporları, hata düzeltme | İki ay üst üste Excel ile kuruşu kuruşuna aynı sonuç |
| **3. Satışa hazır** (3–4 hf) | Banka ekstresi eşleştirme, borç hatırlatma SMS'i, gecikme tazminatı, genel kurul raporu, abonelik/deneme, tanıtım sayfası, yasal metinler, destek kanalı | İlk dış müşteri kendi başına kurulum yapabiliyor |
| **4. Büyüme** | Online ödeme, talep/arıza, duyuru, anket, belgeler, firma paneli, mobil uygulama, API | Müşteri geri bildirimine göre sıralanır |

**Çalışma şekli:** her faz başında kısa bir teknik şartname (bu belgeden), para hesaplayan her
kod için önce test, her hafta staging'e sürüm, her fazın sonunda sizinle demo ve kabul.

---

## 12. Riskler
| Risk | Etki | Önlem |
| --- | --- | --- |
| Kapsam şişmesi (her şeyi yapmaya çalışmak) | Hiçbir şey bitmez | MVP tablosuna sadakat; "sonra" listesi müşteri isteğiyle sıralanır |
| Para hesabında hata | Güven kaybı, hukuki sorun | Çift taraflı defter, değişmez kayıt, property testleri, pilotta paralel çalıştırma, kullanım şartlarında sorumluluk sınırı |
| Veri ihlali | KVKK cezası, itibar | RLS, 2FA, şifreleme, en az yetki, sızma testi (satış öncesi), olay müdahale planı |
| Tek kişi bağımlılığı | Destek ve bakım yükü | Otomasyon, iyi log/izleme, yardım merkezi, sade ürün |
| Rekabet | Satış zorluğu | Küçük-orta site odaklı konum, hızlı kurulum, şeffaflık; pilottan referans |
| SMS maliyeti | Marj düşer | "Beni hatırla", isteğe bağlı şifre, e-posta önceliği, SMS'i plan fiyatına yansıtma |

---

## 13. Sizden beklenen kararlar

| # | Karar | Öneri |
| --- | --- | --- |
| 1 | Teknoloji | **Karar: Django + HTMX + PostgreSQL** (19.11.2026) |
| 2 | Hedef müşteri sırası | Önce küçük-orta siteler, model firmalara hazır (§2) |
| 3 | Sakin girişi | **Karar: Telefon + SMS kodu**, "beni hatırla", isteğe bağlı şifre (§4.1) (19.11.2026) |
| 4 | Barındırma | Türkiye'de sunucu (§3.2) |
| 5 | Depo | **Karar:** `site-yonetim` GitHub'da private depo (19.11.2026). Eski depo portfolyo v1 olarak kalır |
| 6 | Şirket ve yasal | Satış öncesi mali müşavir + avukat görüşmesi (§3.3) |

Kararlar netleşince bu belge "onaylı" olur ve Faz 0'ın teknik şartnamesi yazılır.
