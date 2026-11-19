# Faz 1: Çekirdek muhasebe

Durum: tamamlandı (19.11.2026) · Plan: [URUN_PLANI.md](URUN_PLANI.md) §3, §5, §6, §11

## Kabul kriteri
- [x] Bir sitenin bir ayı baştan sona sistemde yürütülebiliyor: yapı → kişiler → tahakkuk →
      tahsilat + makbuz → gider → borçlular / ekstre → dönem kilidi
- [x] Tüm toplamlar defterle tutarlı: her bölümün ekstre bakiyesi = açık borçlar − avans
      (testlerde her senaryodan sonra ve demo sitesi için denetleniyor); mizan her zaman dengede

## Yapılanlar
| Konu | Karar | Neden |
| --- | --- | --- |
| Para | Her tutar kuruş cinsinden `bigint`; `float` hiçbir yerde yok; ekranda `1.234,56 ₺` | Yuvarlama hatası olmaz |
| Alan katmanı | `domain/money`, `domain/distribution`, `domain/allocation`: Django'dan bağımsız saf fonksiyonlar, hypothesis ile özellik testleri | Para hesaplayan kod tek yerde ve en yoğun test edilen yer |
| Dağıtım | En büyük kalan yöntemi; eşit kalanlarda girdi sırası. Yöntemler: her bölüme aynı tutar, bölüm tipine göre, toplamı eşit / arsa payı / m²'ye göre böl | Σ dağıtılan = girilen toplam, kuruş kaybolmaz |
| Özel aidat | Sabit tutar, % indirim, muaf; başlangıç–bitiş tarihli, karar referanslı, çakışma engelli. Yalnızca "özel aidat uygulanır" işaretli türlerde (varsayılan: Aidat) | Plan §3.1 karar defteri; yakıt gibi tüketim borçlarında muafiyet uygulanmaz |
| Muaflar | Toplam dağıtımında paydaya girmez; sabit tutar/indirim farkı komşulara yüklenmez | Plan §8 |
| Sorumlu kişi | Tahakkukta dönem başında (ayın 1'i) borç sorumlusu olan malik/kiracı anlık görüntü olarak saklanır | Kullanıcı kararı (19.11.2026); ay ortası satış/taşınma |
| Defter | Çift taraflı; kayıt ve satırlar veritabanı tetikleyicisiyle değiştirilemez/silinemez; denge ve "en az iki satır" işlem sonunda veritabanında denetlenir; düzeltme ters kayıtla | Muhasebe kaydı kanıttır |
| Dönem kilidi | Kilitli tarihe kayıt hem serviste hem veritabanında engellenir; kilit yalnızca ileri alınır | Ay kapanışı |
| Hesap planı | Site başına hazır şablon (100 Kasa, 102.xx Banka, 120 Sakin alacakları, 340 Alınan avanslar, 600–649 gelirler, 770.xx gider kalemleri); kodlar Tek Düzen Hesap Planı'na yakın | Mali müşavir tanır; kasa/banka/gider kalemi panelden eklenir |
| Tahakkuk | Taslak → önizleme (bölüm bölüm, sorumlu kişiler, anlaşmalar, eksik arsa payı uyarıları) → onay → iptal (ters kayıt). Aynı bölüm aynı dönemde aynı türden iki kez borçlandırılamaz (servis + kısmi benzersiz indeks); "Özel toplama"/"Diğer" ayda birden çok olabilir | Plan §8 tahakkuk maddeleri |
| Eşleştirme | Önce işaretlenen borçlar, sonra en eski vade, aynı vadede tür önceliği; artan tutar avans (340) olur, yeni tahakkuk onaylanınca otomatik mahsup edilir | Plan §5.5 |
| İptaller | Tahsilat iptali: eşleşmeler pasifleşir (iz kalır), borçlar yeniden açılır, kasa/banka düşer. Tahakkuk iptali: ödenmiş kısımlar avansa döner ve bölümün diğer açık borçlarına mahsup edilir | Geri dönen EFT, yanlış tahakkuk |
| Makbuz | Site ve yıl başına boşluksuz numara (`2026-000123`, satır kilidiyle); iptal edilen makbuz "İPTAL" damgasıyla kalır. Yazdırılabilir HTML (tarayıcıdan PDF kaydedilir) | Kullanıcı kararı: WeasyPrint barındırma kararıyla gelecek |
| Gider | Kategori (gider hesabı), tedarikçi, belge no, kasa/banka; iptal ters kayıtla. Virman kasa ↔ banka | Plan §5.6 |
| Raporlar | Site özeti, bölüm ekstresi (yürüyen bakiye, başlangıç tarihli devreden bakiye), borçlular (gecikme günü), kasa/banka, mizan, yevmiye, hesap hareketleri | Plan §6 MVP raporları |
| Yetki | Yeni yetkiler: `site.view`, `property.manage`, `people.view/manage`, `finance.view`, `billing.manage`, `payments.manage`, `expenses.manage`, `ledger.lock`. Saha görevlisi bölüm/kişi görür, mali ekranları göremez; denetçi okur, yazamaz; siteye özel rol yalnızca kendi sitesinde | Plan §4 |
| Kiracı | 17 yeni tablonun hepsi `TenantModel` + `enable_rls`; `tests/test_rls.py` zorunlu kılar | Faz 0 kuralı |
| KVKK | Denetim izine kişi adı/telefonu yazılmaz (yalnızca "değişti"); borçlular listesi yalnızca yönetime; TCKN alanı yok | Plan §3.2 |
| Demo | `python manage.py seed_demo --org-name "..." --admin-email ...`: tamamen sentetik bir site | Gerçek kişi verisi depoya girmez |

## Bilinçli olarak sonraya bırakılanlar
- **Makbuz/ekstre PDF dosyası (WeasyPrint):** sistem kütüphanesi (pango) gerektiriyor; Docker/barındırma kararıyla.
- **Belge eki (fatura, dekont dosyası):** dosya deposu (S3 uyumlu) kararıyla.
- **Sayaç/yakıt dağıtımı, bütçe (işletme projesi), bölüm grubu, kişi sayısı anahtarı.**
- **Banka ekstresi içe aktarma ve eşleştirme, gecikme tazminatı:** Faz 3.
- **Açılış bakiyeleri ve Excel'den kurulum:** Faz 2 (hesap planında `500 Açılış bakiyeleri` hazır).
- **Kişi anonimleştirme ekranı:** model alanı hazır, akış Faz 2'de sakin portalıyla.
- **Ödenmemiş gider (tedarikçi borcu):** şimdilik gider ödendiği anda kaydedilir.

## Sonraki adım: Faz 2 (kurulum ve sakin)
Excel kurulum sihirbazı, açılış bakiyeleri, sakin portalı (telefon + SMS kodu), gizlilik ayarları,
e-posta bildirimi.
