# ✈️ UcuzGez: kişisel ucuz bilet ve esnek gezi bulucu

En fazla 50 kişilik kapalı bir grup için yazılmış, ticari olmayan bir araç. Ne yapıyor:

- **Nereye gideceğini önerir:** Kalkış noktasından (şehir kodu ya da **konumun**) tarih aralığında **her yere** en ucuz uçuşları tarar.
- **Kaç gün kalacağını esnek tutar:** "3-6 gün" dersin; olası bütün gidiş/dönüş kombinasyonlarını dener.
- **Farklı şehirden dönüş önerir (açık çene):** Viyana'ya uçup Prag'dan dönmek daha ucuzsa bunu, otobüs/tren süresi ve ücret tahminiyle birlikte gösterir. Aynı tarihlerde Viyana'dan dönmenin ne kadar tutacağını ve aradaki farkı da yazar.
- **Takip + bildirim:** Bir arama kaydedilir, sistem birkaç saatte bir yeniden kontrol eder. Fiyat hedefinin altına inerse ya da son 30 günün normalinden belirgin ucuzlarsa **Telegram'dan** haber verir.
- **Fırsat radarı:** Her sabah evinden her yere tek yön fiyatlara bakar. Rotanın normalinden %35+ ucuz olan bir uçuş varsa haber verir.
- **Pegasus, AJet ve diğerleri:** Veri kaynağında görünen bütün havayolları gelir. İstersen havayoluna göre süzebilirsin.

Cronjob gerekmez. Zamanlayıcı uygulamanın içinde çalışır: **tek süreç = web + Telegram botu + zamanlayıcı + SQLite.**

```
            ┌──────────── tek Python süreci ────────────┐
Telegram ◄─►│ telegram_bot ─┐                           │
Tarayıcı ◄─►│ web (Starlette)├─► services ─► planner ──►│─► Travelpayouts (önbellekli, ücretsiz)
            │ scheduler ────┘        │          │       │─► SerpApi / Duffel (canlı doğrulama, opsiyonel)
            │                        ▼          ▼       │
            │                    SQLite      ground.py  │  (otobüs/tren tahmini)
            └───────────────────────────────────────────┘
```

## Veri kaynakları (Kasım 2026 durumu)

| Kaynak | Ne için | Ücret | Not |
|---|---|---|---|
| **Travelpayouts / Aviasales Data API** | Ana tarama ("her yere", takip, radar) | Ücretsiz | Son ~48 saatte görülen **önbellek** fiyatları. Keşif için ideal, satın almadan önce linkten kontrol et. |
| SerpApi (Google Flights) | En iyi N sonucu canlı doğrulama | Ücretsiz kotası var, sonrası ücretli | `SERPAPI_KEY`, `UG_VERIFY_TOP_N=3` |
| Duffel | Canlı doğrulama (Pegasus Travelport üzerinden var) | Arama başına küçük ücret | `DUFFEL_TOKEN` |
| `mock` | Anahtarsız deneme | – | Sahte ama tutarlı veri |

Neden Amadeus ya da Kiwi değil? Amadeus self-service API'leri 17 Temmuz 2026'da kapandı. Kiwi Tequila ise artık yalnızca anlaşmalı iş ortaklarına açık. Pegasus ve AJet'in sitelerini kazımak (scraping) kullanım şartlarına aykırı ve sık kırılıyor. O yüzden sistem, sağlayıcı eklenebilir bir yapıda kuruldu: `providers/` altına `fetch_month()` uygulayan yeni bir sınıf eklemen yeterli.

## Hızlı başlangıç (kendi bilgisayarında)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # UG_PROVIDER=mock ile anahtarsız da çalışır
python -m ucuzgez.cli add-user "Mansur" --admin   # web linki + Telegram davet kodu verir
python -m ucuzgez                  # http://localhost:8000
python -m ucuzgez.cli search IST 01.12 20.12 3-5  # terminalden arama
pytest -q
```

### Anahtarlar
1. **Travelpayouts:** travelpayouts.com → kayıt → *Aviasales* programına katıl → API token → `TRAVELPAYOUTS_TOKEN`, `UG_PROVIDER=travelpayouts`
2. **Telegram:** @BotFather → `/newbot` → token → `TELEGRAM_BOT_TOKEN`
3. (Opsiyonel) Dünyadaki bütün havalimanlarını indir: `python -m ucuzgez.cli update-airports` (yerleşik liste Türkiye, Avrupa ve çevresindeki ~95 havalimanı)

## Telegram komutları

```
/start KOD                           hesabı bağla (davet kodu)
/konum                               konum paylaş → yakın havalimanlarından arar
/kesfet 3-5                          konumundan önümüzdeki 60 gün, 3-5 günlük en ucuz geziler
/ara IST 01.12 20.12 3-5             tarih aralığı + gün aralığı
/ara IST 01.12 31.12 3-7 VIE PRG     sadece bu hedefler
/ara SAW 01.12 20.12 2-4 direkt pegasus ajet yurtdisi
/takip IST 3-5 4000                  tarihsiz = sürekli önümüzdeki 60 gün, 4000 TL altına düşünce yaz
/takipler · /sil 3 · /radar · /yardim
```

## Nasıl çalışıyor? (planner.py)

1. Kalkış şehir(ler)inden tarih penceresi boyunca tek yön en ucuz uçuşlar çekilir (ay bazında, önbellekli).
2. En ucuz 12 varış şehri aday olur.
3. Her aday **D** için dönüş şehirleri belirlenir: D'nin kendisi ve kara yoluyla en fazla `UG_MAX_GROUND_HOURS` saatte ulaşılabilen komşu şehirler (en fazla 6 tane). Popüler hatlar (Viyana–Prag, Viyana–Bratislava…) için tipik süre ve ücret elle girildi, diğerleri mesafeden tahmin edilir. Deniz geçişleri hariç tutulur.
4. `gidiş(gün₁) + kara yolu + dönüş(gün₁+n)` toplamları n ∈ [min, max] için hesaplanır.
5. Sonuçlar sıralanır ve çeşitlendirilir (aynı şehirden en fazla 2 sonuç). İsteğe bağlı olarak en iyi N sonuç canlı fiyatla doğrulanır.

Bir aramanın maliyeti ≈ 1 + (12 şehir × ~4 dönüş şehri) ay-sorgusu. Sonuçlar 6 saat önbellekte tutulduğu için 50 kişi aynı yerleri aradığında API'ye tekrar gidilmez.

---

## ▲ Vercel'de ücretsiz, kartsız, sunucusuz kurulum (önerilen)

Bu modda arka planda sürekli açık bir süreç gerekmez. Telegram mesajları webhook ile gelir, zamanlayıcı dışarıdan tetiklenir, veriler Turso'da (ücretsiz uzak SQLite) durur. Kullanılan her şey ücretsiz ve kart istemiyor.

```
Telegram ──webhook──► Vercel (api/index.py → ucuzgez/serverless.py) ◄── tarayıcı
GitHub Actions (3 saatte 1) ──► /cron ─┘          │
Vercel Cron (günde 1, yedek)  ──► /cron           ▼
                                           Turso (SQLite, HTTP)
```

1. **Turso:** app.turso.tech → GitHub ile giriş → *Create database* (bölge: Frankfurt) → veritabanı URL'sini (`libsql://...turso.io`) ve bir *token* al.
2. **Vercel:** vercel.com → GitHub ile giriş → *Add New → Project* → `ticket-tracking` reposunu *Import* et → *Environment Variables* bölümüne şunları gir:
   | Değişken | Değer |
   |---|---|
   | `UG_PROVIDER` | `travelpayouts` (anahtar yoksa önce `mock`) |
   | `TRAVELPAYOUTS_TOKEN` | Travelpayouts token |
   | `TELEGRAM_BOT_TOKEN` | @BotFather token |
   | `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` | 1. adımdan |
   | `CRON_SECRET`, `TELEGRAM_WEBHOOK_SECRET` | iki ayrı rastgele uzun dize |
   | `UG_EUR_RATE` | `54` (arada güncelle) |

   → **Deploy**. Adresin `https://PROJE.vercel.app` olur.
3. **Telegram'ı bağla (tek seferlik):** tarayıcıda `https://PROJE.vercel.app/tg/kur?key=CRON_SECRET` adresini aç. `{"ok":true}` görmelisin.
4. **İlk kullanıcı:** `https://PROJE.vercel.app/yonetim/kullanici?key=CRON_SECRET&ad=Mansur` web giriş linkini ve Telegram davet kodunu verir. Aynı adres `ad` parametresi olmadan açılırsa bütün kullanıcıları listeler.
5. **3 saatte bir kontrol:** GitHub'da repo → *Settings → Secrets and variables → Actions* → `APP_URL` (=`https://PROJE.vercel.app`) ve `CRON_SECRET` ekle. `.github/workflows/cron.yml` otomatik çalışır. Vercel Hobby cron'u günde yalnız bir kez çalıştırdığı için bu adım gerekli.

Notlar: Vercel Hobby'de bir fonksiyon en fazla 300 sn çalışabilir. `/cron` 240 sn'de durur, yarım kalan iş bir sonraki tetiklemede devam eder. Telegram bir mesajı tekrar gönderirse (`update_id`) ikinci kez işlenmez. Vercel Hobby kişisel ve ticari olmayan kullanım içindir; bu proje de öyle.

## 💸 Alternatif: kendi sunucunda 7/24 (Oracle Always Free / ev bilgisayarı)

Bu uygulama sürekli açık bir süreç istiyor (Telegram long-polling + zamanlayıcı). O yüzden "boşta kalınca uyuyan" ücretsiz platformlar uygun değil. Render'ın ücretsiz servisi 15 dakika istek gelmezse uyuyor ve diski kalıcı değil. Bu durumda SQLite verileri yeniden başlatmada kaybolur.

### ✅ Önerilen: Oracle Cloud "Always Free" VM
- **Always Free** hesapta süresiz ücretsiz: 2 adet AMD Micro (1/8 OCPU, 1 GB RAM). Yeni hesaplarda ARM A1 kotası 2 OCPU / 12 GB'a indirildi (Haziran 2026). Bu uygulama Micro'da bile rahat çalışır.
- Kayıtta kredi kartı **doğrulama için** istenir. **Hesabı "Pay As You Go"ya yükseltmezsen** ve sadece "Always Free Eligible" etiketli kaynakları kullanırsan ücret çıkmaz.
- Dikkat: Oracle, uzun süre neredeyse hiç CPU kullanmayan Always Free makineleri geri alabileceğini (durdurabileceğini) söylüyor. Haftalık yedek al (`deploy/backup.sh`). Makine durdurulursa konsoldan tekrar başlatabilirsin.

Kurulum:
1. cloud.oracle.com → Free Tier hesabı → *Compute → Create instance* → Image: **Ubuntu 24.04**, Shape: **VM.Standard.E2.1.Micro** (ya da A1.Flex 1 OCPU / 6 GB) → SSH anahtarını ekle.
2. Sunucuda:
   ```bash
   # repo private ise önce GitHub'da deploy key ya da token ayarla
   git clone https://github.com/mansuruzer/ticket-tracking.git /tmp/tt && bash /tmp/tt/deploy/setup.sh
   nano /opt/ucuzgez/.env        # TRAVELPAYOUTS_TOKEN, TELEGRAM_BOT_TOKEN ...
   sudo systemctl restart ucuzgez
   ```
3. **Web arayüzüne erişim (port açmadan, ücretsiz):** En kolayı SSH tüneli: `ssh -L 8000:localhost:8000 ubuntu@SUNUCU_IP`, sonra tarayıcıda `localhost:8000`. Arkadaşların da erişsin istiyorsan Cloudflare Tunnel (ücretsiz) ya da Oracle güvenlik listesinden 8000 portunu aç. Telegram botu için hiçbir port açmana gerek yok.
4. Yedek: `crontab -e` → `30 3 * * * /opt/ucuzgez/deploy/backup.sh`

### Alternatifler
- **Evdeki bir makine / Raspberry Pi / eski laptop:** Elektrik dışında maliyeti yok. Aynı `setup.sh` çalışır.
- **Koyeb free:** Uyumayan 1 servis veriyor (512 MB), ama kart istiyor ve disk kalıcı değil. Bu yüzden takip verisi yeniden dağıtımda silinir.
- **Google Cloud e2-micro:** Sadece ABD bölgelerinde süresiz ücretsiz, 1 GB RAM.

## Sınırlar / dürüst notlar
- Travelpayouts fiyatları **önbellek** verisidir. Bazen kaybolmuş bir fiyat ya da hiç aranmamış bir rota olabilir. Canlı fiyat için SerpApi/Duffel doğrulamasını aç.
- Kara yolu ücreti ve süresi **tahmindir** (FlixBus/tren seviyesinde). Satın almadan önce kontrol et.
- Fırsat radarı anlamlı karşılaştırma yapabilmek için birkaç gün fiyat geçmişi biriktirmeli (rota başına en az 5 gözlem).
- Bilet satmaz. Linkler Aviasales, Google Flights ve havayolu sitelerine yönlendirir.
