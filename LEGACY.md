# 🧠 Wasiat Agentic Discord Bot — Panduan Warisan

> Dokumen ini adalah **single source of truth** untuk setup, konfigurasi, dan maintenance Discord Bot.
> Jika Hermes reboot atau diganti, baca ini dulu sebelum melakukan apapun.

---

## 📌 Identitas Pemilik

- **Nama**: Jefri Herdi Triyanto (panggil: **Pak / Bapak** — JANGAN panggil "bos" atau "Jefri")
- **Peran**: Sysadmin / Developer — PC Sawang (192.168.69.2)
- **Bahasa**: Bahasa Indonesia, profesional tidak bertele-tele
- **Konfirmasi**: "Siap Noted Pak!" atau "Siap Laksanakan Pak!"
- **GitHub**: https://github.com/jefripunza

---

## 🏗️ Arsitektur

```
┌─────────────────────────────────────────────────────┐
│  Cloudflare Tunnel → discord-bot.sawang.tech:8899   │
│         ↓                                           │
│  Express Server (Node.js, port 8899)                │
│  ┌──────────────┐   ┌──────────────────────────┐   │
│  │ app.js       │◄──│ MCP Client → discord_mcp  │   │
│  │ (interaksi) █│   │ .py (Discord REST API)   │   │
│  │ AI via       │   └──────────────────────────┘   │
│  │ 9ROUTER API  │   ┌──────────────────────────┐   │
│  └──────────────┘   │ monitor.py (cronjob)     │   │
│                      │ check-error.py (cron)   │   │
│                      └──────────────────────────┘   │
│  Channel: #monitoring-elite-global                  │
│           #ai-response, #bot-error, #command        │
└─────────────────────────────────────────────────────┘
```

---

## 🚀 Setup Awal

### 1. Clone & Install
```bash
cd /home/sawang/workspace
git clone https://github.com/jefripunza/agentic-discord-bot.git discord-bot
cd discord-bot
npm install
```

### 2. Environment (.env)
Simpan di `/home/sawang/workspace/discord-bot/.env`:
```env
APP_ID=1516966528317128705
CLIENT_ID=1516966528317128705
PUBLIC_KEY=aa4fdb0058f07cb33ae1255a41fa5ab5c45842b880ca3a69d7d0c755cdc5552b
DISCORD_TOKEN=<ISI_TOKEN_BOT>
GUILD_ID=1516963494636027924
AI_API_KEY=<9ROUTER_API_KEY>        # opsional — fallback ke hex-encoded key di app.js
AI_BASE_URL=https://ai.jefripunza.com/v1
AI_MODEL=agent
```

> **Credential backup**: `/home/sawang/credentials/discord_bot.txt`

### 3. Register Slash Commands
```bash
cd /home/sawang/workspace/discord-bot && npm run register
```
3 commands: `/create`, `/edit`, `/prompt`

### 4. Jalankan Bot
```bash
cd /home/sawang/workspace/discord-bot && node app.js
```
Port: **8899**

### 5. Set Interactions Endpoint URL
Di **Discord Developer Portal** → Application → General Information:
```
https://discord-bot.sawang.tech/interactions
```

---

## 📊 Monitoring Cronjob

### Jadwal
**07:00, 12:00, 20:00 WIB** setiap hari → kirim ke **#🚁┃monitoring-elite-global**

### Script
```
/home/sawang/workspace/discord-bot/mcp-server/monitor.py
```

### Crontab
```cron
0 7,12,20 * * * cd /home/sawang/workspace/discord-bot && /home/sawang/.hermes/hermes-agent/venv/bin/python mcp-server/monitor.py >> /home/sawang/.hermes/logs/monitor-cron.log 2>&1
```

### Isi Laporan
- 📊 **LAPORAN MONITORING** — judul
- 🗓️ Hari Jawa + tanggal + jam WIB
- 🥇 **HARGA EMAS ANTAM** — buyback, jual, spread
- 💱 **NILAI TUKAR** — USD, CNY, RUB, BRICS
- 🤖 **PROMO AI** — harga token model AI
- 📰 **BERITA** — 4 top headlines (Google News + Detik)
- 📰 **SENTIMEN** — keyword-based (Bullish/Neutral/Bearish)
- 📊 **REKOMENDASI** — JUAL/BELI/TAHAN based on spread %
- 🔘 **2 Button** — "📈 Jual" dan "📉 Beli"

### Button Handler (di app.js)
Kalau user klik Jual/Beli:
1. Disable semua button (cegah double-click)
2. AI analysis harga emas + profit/loss estimation
3. Kirim analisa ke **#🤖┃ai-response**
4. Reply ke user bahwa hasil ada di ai-response

---

## 🔍 Error Monitoring

### Script
```
/home/sawang/workspace/discord-bot/mcp-server/check-error.py
```

### Fungsi
Baca `/home/sawang/.hermes/logs/discord-bot-error.log`, kirim error baru ke **#🐞┃bot-error**.

### State tracking
Offset byte disimpan di `~/.hermes/scripts/.monitor-error-state`

---

## ⚙️ Slash Commands Detail

### `/create <nama>`
Buat text channel baru di category "New Channel" (parent: 1516963495499792475).

### `/edit <channel> <instruksi>`
Edit channel: rename, delete, set topic, atau instruksi bebas via AI.

### `/prompt <prompt>`
Prompt bebas untuk Hermes. Bisa:
- **Regex action**: create/delete/rename/send message (detected via keyword)
- **Query**: forward ke Hermes AI
- **Unknown**: AI auto-detect intent → action
Hanya bisa di **#🗣️┃command** (channel_id: 1516965584296874156).

---

## 🤖 AI Configuration

- **Provider**: 9Router (https://ai.jefripunza.com/v1)
- **Fallback key**: hex-encoded di app.js (untuk backward compat)
- **Model**: `agent` (Hermes)
- **Timeout**: 30s, retry 2x dengan exponential backoff

---

## 🔐 Credential Storage

| Item | Lokasi |
|---|---|
| Discord Bot | `/home/sawang/credentials/discord_bot.txt` |
| GitHub PAT | `/home/sawang/credentials/github_pat.txt` |
| Coolify API | `/home/sawang/credentials/coolify_api.txt` |
| Discord .env | `/home/sawang/workspace/discord-bot/.env` |

> Semua credential juga di **Hindsight memory** (bank: hermes-memories).

---

## 🌐 Jaringan & Tunnel

- **Cloudflare Tunnel**: discord-bot.sawang.tech → localhost:8899
- **Bot server port**: 8899
- **Interactions Endpoint**: `https://discord-bot.sawang.tech/interactions`

---

## 🐍 Cronjob Convention (WAJIB)

1. Setiap cronjob **WAJIB** implementasi di **Python script**
2. Tugas cronjob hanya **trigger Python script** (jangan logic di shell/cron)
3. Python script boleh akses **LLM/AI** untuk analisa
4. Script Python disimpan di folder proyek git masing-masing
5. Cronjob terdaftar di crontab Bapak (bukan root)

---

## 🆘 Troubleshooting

### Bot tidak merespon
1. Cek `curl http://localhost:8899/` — harus 404 (normal, cuma handle POST /interactions)
2. Cek `process(action='poll', session_id=...)` — harus running
3. Cek log: `~/.hermes/logs/discord-bot-error.log`

### MCP Connection failed
Pastikan:
- Path Python: `~/.hermes/hermes-agent/venv/bin/python`
- Path MCP: `~/workspace/discord-bot/mcp-server/discord_mcp.py`
- DISCORD_TOKEN ter-pass ke env MCP (fix di app.js line 19: `env: { ...process.env, DISCORD_TOKEN: process.env.DISCORD_TOKEN }`)

### Cronjob tidak jalan
1. Cek `crontab -l` — apakah terdaftar?
2. Cek `~/.hermes/logs/monitor-cron.log`
3. Test manual: `cd ~/workspace/discord-bot && ~/.hermes/hermes-agent/venv/bin/python mcp-server/monitor.py`

---

## 📁 Repository

**GitHub**: https://github.com/jefripunza/agentic-discord-bot
**Branch**: main

### Push perubahan
```bash
cd /home/sawang/workspace/discord-bot
git add -A
git commit -m "deskripsi perubahan"
git push
```
> GitHub PAT: `/home/sawang/credentials/github_pat.txt`
> Username: jefripunza

---

## 🔮 Masa Depan

- Semua script bot Discord disimpan di repo ini
- Dokumen ini adalah **WASIAT** — baca dulu sebelum modifikasi besar
- Kalau ragu, tanya Pak Jefri
- Simpan pengetahuan baru ke **Hindsight** (bank: hermes-memories)
- Cronjob convention: Python script, bukan shell

---

*Dibuat: 24 Juli 2026*
*Update terakhir: 24 Juli 2026*
*Oleh: Hermes Agent untuk Pak Jefri*
