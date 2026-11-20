# Faz 2: Kurulum ve sakin

Durum: tamamlandı (20.11.2026) · Plan: [URUN_PLANI.md](URUN_PLANI.md) §3.2, §4.1, §6, §9, §11

## Kabul kriteri
- [x] Pilot site Excel'den kurulabiliyor: kurulum şablonu (yapı + kişiler + açılış borçları) ve
      eski aylık aidat çizelgesi yükle → önizle → onayla. Tarayıcıda sentetik 6 bölümlük site
      iki dosyayla birkaç dakikada kuruldu; borçlular ve mizan defterle tutarlı.
- [x] Sakinler kendi ekstresini görüyor: telefon + SMS kodu ile giriş, bölümlerim, açık borçlar,
      ödemeler ve makbuzlar, ekstre; başka bölüm 404.

## Yapılanlar
| Konu | Karar | Neden |
| --- | --- | --- |
| İçe aktarma akışı | `ImportJob` (RLS): yükle → çözümle → önizle (veritabanına yazmaz) → onayla (tek transaction; planı yeniden kurar, hata varsa hiçbir şey yazmaz). Aynı dosya ikinci kez yüklenirse uyarı | Kurulumda yanlış veri en pahalı hata |
| Kişisel veri | Dosya sunucuda saklanmaz; çözümlenen satırlar yalnızca önizleme süresince tutulur, onay ya da vazgeçince silinir | KVKK veri minimizasyonu |
| Kurulum şablonu | İndirilebilir `.xlsx`: Bölümler (blok, no, kat, tür, tip + aidatı, m², arsa payı, malik/kiracı, borç sorumlusu), Açılış bakiyeleri (dönem, tutar; eksi = avans), Açıklamalar. Başlıklar Türkçe ve esnek eşleşir | Müşteri kendi başına kurabilsin |
| Kişi eşleştirme | Aynı ad + aynı telefon = aynı kişi (iki dairenin maliki); telefonsuz kişiler her satırda ayrı kişi | Aile üyelerini yanlışlıkla birleştirmemek |
| Mevcut bölüm | Zaten kayıtlı bölüm değiştirilmez (uyarı); açılış bakiyesi yine uygulanır | Aynı dosyayla yalnızca bakiye yüklenebilsin |
| Aylık çizelge | Eski `aidat_takip` hücre çözümleyicisi `domain/cells.py`'ye taşındı (kısmi, iade, `350+230`, belirsiz binlik ayracı, tarih yazılmış hücre bayrakları). Ödemeler en eski aydan kapatılır; kalan aylar yaşlandırmalı açılış borcu, fazlası avans olur. Aylık borç bölüm tipinin aidatı (yoksa girilen tutar), özel aidat anlaşması uygulanmış | Excel'le paralel çalıştırmada aynı sonuç |
| Bölüm eşleştirme | `A Blok · 5`, `A-5`, `a5`, `A blok no:5` aynı bölüm; birden çok bölümle eşleşen ya da bulunamayan satır hata | Sessiz yanlış eşleşme olmasın |
| Açılış bakiyeleri | Borç: "Devreden borç" türünde ay ay borç kalemi (120 / 500 Açılış bakiyeleri). Avans: makbuz numarası tüketmeyen "Açılış avansı" tahsilatı (500 / 340), yeni tahakkukta otomatik mahsup. Site özetinde bu ayın tahakkuk/tahsilatına sayılmaz. İptal mevcut ters kayıt akışıyla | Eşleştirme ve ekstre ilk günden doğru |
| Dönem kilidi | Kurulumda açılış tarihine kadar kilitlenebilir (muhasebe yetkisi olanlar); birden çok dosyada sonuncusunda | Devreden bakiye sonradan bozulmasın |
| Sakin girişi | Cep telefonu → 6 haneli SMS kodu (HMAC özeti, 5 dk, 5 deneme, telefon başına 15 dakikada 3, IP başına saatte 10 kod). Kayıtsız numaraya SMS gitmez ama ekran aynıdır. İsteğe bağlı şifre (aynı kilitleme), "30 gün hatırla" | Plan §4.1 kararı; numara taraması ve SMS maliyeti |
| Sakin kimliği | `ResidentAccount` / `OtpChallenge` RLS dışında (giriş anında kuruluş bilinmez; `User`/`Membership` ile aynı gerekçe); kişi ya da bölüm verisi içermez. Erişim her istekte kiracı bağlamında yöneticinin kaydından hesaplanır | İlişki bitince erişim de anında biter |
| Görünürlük | Sakin yalnızca bugün geçerli ilişkisi olan bölümleri, ilişkisinin başladığı tarihten itibaren görür (önceki malikin makbuzları ve ödeme ayrıntısı görünmez, "Devreden bakiye" satırı görünür). Birden çok yönetimde kaydı olan numara önce yönetim seçer | KVKK; kiracı/malik değişimi |
| Aydınlatma metni | İlk girişte veri sorumlusu (kuruluş) başına onay; sürümüyle kaydedilir (`ResidentConsent`, RLS) | Plan §9 |
| SMS sağlayıcısı | Arayüz arkasında: geliştirmede konsol, testte bellek, canlıda sağlayıcı ayarlanana kadar kapalı (kod sessizce kaybolmaz). Yalnızca bilgilendirme SMS'i | Sağlayıcı sözleşmesi barındırma kararıyla |
| Gizlilik ayarları | Site başına `PortalSettings` (RLS): portal açık/kapalı, anonim tahsilat oranı, kasa+banka toplamı, kategori bazlı yıllık giderler, iletişim notu. Özet seçenekleri varsayılan kapalı; borçlu listesi hiçbir seçenekte yok | Plan §3.2 |
| E-posta | Tahsilatta makbuz e-postası (ödeyen belliyse yalnızca ona, değilse e-postası olan ve "bildirim alır" işaretli malik/kiracılara); bölüm sayfasından ekstre e-postası. Her gönderim `Notification` (RLS) olarak kayıtlı; hata tahsilatı bozmaz | Plan §6 Bildirim (MVP: e-posta) |
| Anonimleştirme | Tüm ilişkisi bitmiş kişide: ad/telefon/e-posta/not silinir, tahakkuktaki sorumlu adı ve bildirim alıcısı temizlenir, numara başka kayıtta yoksa sakin hesabı silinir. Makbuzdaki ödeyen adı muhasebe belgesi olduğu için kalır | KVKK; yasal saklama |
| Yetki | `setup.manage` (kuruluş/site yöneticisi); portal ayarları `site.update`; ekstre e-postası `payments.manage` | Plan §4 |
| Kiracı | 4 yeni RLS'li tablo: `ImportJob`, `ResidentConsent`, `PortalSettings`, `Notification` | Faz 0 kuralı |

## Bilinçli olarak sonraya bırakılanlar
- **Gerçek SMS sağlayıcısı** (Netgsm, İleti Merkezi…): sözleşme ve barındırma kararıyla; yalnızca
  bir sınıf eklenecek.
- **Arka plan kuyruğu:** e-posta şimdilik istek içinde gönderiliyor (gönderim hatası kaydedilir).
  Toplu gönderim gelince Celery/RQ.
- **PDF ekstre/makbuz ve e-posta eki:** WeasyPrint kararıyla.
- **Sakin bildirim tercihleri ekranı** (SMS/e-posta/hiçbiri): şimdilik yönetici "bildirim alır"
  işaretini kullanır.
- **Hisseli malik oranlarına göre bildirim**, tapu listesi içe aktarma, açılış bakiyesini ekrandan
  tek tek girme (şimdilik Excel ile).
- **Aydınlatma metni:** örnek metin hukukçu tarafından gözden geçirilmeli ve kuruluş kendi metnini
  girebilmeli.

## Sonraki adım: pilot
Kendi sitenizde Excel ile paralel çalıştırma: aylık çizelgeden açılış, iki ay boyunca fark raporu.
