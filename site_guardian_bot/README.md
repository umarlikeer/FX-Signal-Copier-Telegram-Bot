# 🤖 Site Guardian — Claude bilan saytni boshqaruvchi Telegram bot

VPS serverga o'rnatiladi va:

- **Doimiy monitoring** — sayt ishlayaptimi, tezligi, SSL muddati, CPU/RAM/Disk. Muammo bo'lsa darhol Telegramga xabar.
- **Claude AI agent** — oddiy matn bilan yozasiz ("saytga aloqa formasi qo'sh"), Claude tahlil qiladi, kodni o'rganadi, fayllarni yaratadi/tahrirlaydi, terminal buyruqlarini bajaradi va natijani tekshiradi.
- **Terminal** — `/run <buyruq>` orqali to'g'ridan-to'g'ri.
- **Fayl/papka** — yaratish, o'qish, tahrirlash; Telegram orqali fayl yuklash va yuklab olish.
- **Avto-diagnoz** — sayt tushsa, Claude o'zi loglarni ko'rib sababini topadi (xohlasangiz o'zi tuzatadi).

## 🔐 Xavfsizlik (muhim!)

Bu bot serverni **to'liq boshqaradi**, shuning uchun:

1. **Faqat `ADMIN_IDS` dagi foydalanuvchilar** botdan foydalana oladi. Boshqalar rad etiladi.
2. **Xavfli amallar tasdiq so'raydi** (`CONFIRM_MODE=dangerous`): `rm -rf`, `systemctl stop`, `reboot`, `DROP TABLE`, `git reset --hard` va h.k. uchun Telegramda ✅/❌ tugmalari chiqadi.
3. **Har bir fayl o'zgarishidan oldin zaxira** `BACKUP_DIR` ga saqlanadi.
4. `.env` faylini hech kimga bermang, `chmod 600` qiling. Bot tokeningiz o'g'irlansa ham, begona odam ADMIN_IDS da bo'lmagani uchun buyruq bera olmaydi — lekin tokenni baribir darhol @BotFather orqali yangilang.
5. Telegram akkauntingizda **2 bosqichli parol** (Two-Step Verification) yoqing — akkauntingiz = serveringiz kaliti.

## 🚀 O'rnatish

### 1. Kerakli narsalar
- **Telegram bot token**: [@BotFather](https://t.me/BotFather) → `/newbot`
- **Telegram ID'ingiz**: [@userinfobot](https://t.me/userinfobot) ga yozing
- **Anthropic API kalit**: https://console.anthropic.com → API Keys (Claude.ai obunasi emas — API alohida, to'lov "Billing" bo'limida)

### 2. Serverga yuklash va o'rnatish
```bash
git clone https://github.com/umarlikeer/FX-Signal-Copier-Telegram-Bot.git
cd FX-Signal-Copier-Telegram-Bot/site_guardian_bot
sudo bash deploy/install.sh
```

### 3. Sozlash
```bash
sudo nano /opt/site-guardian/.env
```
Kamida to'ldiring: `TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, `ANTHROPIC_API_KEY`, `SITE_URLS`, `SITE_DIR`, `LOG_FILES`.

### 4. Ishga tushirish
```bash
sudo systemctl restart site-guardian
sudo journalctl -u site-guardian -f
```
Telegramda botga `/start` yozing.

## 💬 Foydalanish

| Buyruq | Vazifasi |
|---|---|
| oddiy matn | Claude'ga topshiriq (tahlil + bajarish) |
| `/status` | Sayt va server holati |
| `/run <buyruq>` | Terminal buyrug'i |
| `/ls [papka]`, `/cat <fayl>` | Fayllarni ko'rish |
| `/logs [n]` | Loglarning oxirgi qatorlari |
| `/diagnose` | Claude to'liq tekshiruv o'tkazadi (hech narsani o'zgartirmaydi) |
| `/monitor on\|off` | Monitoringni yoqish/o'chirish |
| `/new` | Claude bilan yangi suhbat |
| fayl yuborish | `SITE_DIR/uploads/` ga saqlanadi; izoh (caption) yozsangiz, Claude u bilan ishlaydi |

Misollar:
- *"Bosh sahifaga WhatsApp tugmasi qo'sh"*
- *"Sayt nega 502 beryapti? Tuzat"*
- *"Har kuni tunda ma'lumotlar bazasidan zaxira oladigan cron yoz"*
- *"nginx konfiguratsiyasida gzip va keshlashni yoq"*

## ⚙️ Asosiy sozlamalar (`.env`)

| O'zgaruvchi | Ma'nosi |
|---|---|
| `CONFIRM_MODE` | `dangerous` (tavsiya) / `all` / `none` |
| `AUTO_DIAGNOSE` | Sayt tushsa Claude avtomatik tahlil qilsinmi |
| `AUTO_FIX` | Tahlildan keyin o'zi tuzatsinmi (xavfli amallar baribir tasdiq so'raydi) |
| `CLAUDE_MODEL` | Standart: `claude-opus-5-5` |
| `CLAUDE_EFFORT` | `low` / `medium` / `high` / `xhigh` / `max` — yuqorisi aqlliroq, lekin qimmatroq |
| `CHECK_INTERVAL` | Tekshiruv oralig'i, sekund |

## 💰 Narx haqida
Claude API ishlatilgan token uchun to'lanadi. Har bir topshiriq odatda bir necha sentdan bir necha o'n sentgacha turadi (vazifa murakkabligiga qarab). Monitoringning o'zi (HTTP tekshiruv) bepul — Claude faqat siz yozganda yoki `AUTO_DIAGNOSE` ishlaganda chaqiriladi. Console'da oylik limit qo'yib qo'ying.

## 📁 Tuzilma
```
site_guardian_bot/
├── bot.py        # Telegram handlerlar, tasdiq tugmalari, monitoring job
├── agent.py      # Claude agent sikli va asboblar ta'rifi
├── tools.py      # terminal, fayllar, sayt tekshiruvi, xavfli buyruq aniqlash
├── monitor.py    # holat kuzatuvi va ogohlantirishlar
├── config.py     # .env o'qish
└── deploy/       # install.sh va systemd servis
```
