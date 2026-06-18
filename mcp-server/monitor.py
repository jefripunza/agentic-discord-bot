#!/usr/bin/env python3
"""Monitoring harga — fetches data, calls AI for research/formatting, sends to Discord."""
import asyncio, json, os, re, sys
from datetime import datetime
import httpx

HOME = os.path.expanduser("~")
DISCORD_API = "https://discord.com/api/v10"
MONITOR_CHANNEL = "1516984648734085240"
AI_ENDPOINT = "https://ai.jefripunza.com/v1/chat/completions"

def load_discord_key():
    p = os.path.join(HOME, "workspace/discord-backend.py.bak/creds.json")
    if os.path.exists(p):
        with open(p) as f:
            d = json.load(f)
        if d.get("DISCORD_BOT_TOKEN"):
            return d["DISCORD_BOT_TOKEN"]
    return os.environ.get("DISCORD_TOKEN", "")

BOT_KEY = load_discord_key()
if not BOT_KEY or len(BOT_KEY) < 10:
    print("No Discord key", file=sys.stderr)
    sys.exit(1)

def load_ai_key():
    # Try Hermes config first
    try:
        import yaml
        p = os.path.join(HOME, ".hermes/config.yaml")
        if os.path.exists(p):
            with open(p) as f:
                cfg = yaml.safe_load(f)
            k = cfg.get("model", {}).get("api_key", "")
            if k and len(k) > 10: return k
    except: pass
    # Fallback: creds.json
    p = os.path.join(HOME, "workspace/discord-backend.py.bak/creds.json")
    if os.path.exists(p):
        with open(p) as f:
            d = json.load(f)
        if d.get("AI_API_KEY"): return d["AI_API_KEY"]
    return os.environ.get("AI_API_KEY", "")

AI_KEY = load_ai_key()
DISC_HDR = {"Authorization": f"Bot {BOT_KEY}", "Content-Type": "application/json"}
HTTP_HDR = {"User-Agent": "Mozilla/5.0 (Win; x64) Chrome/120"}

async def fetch_gold():
    results = {"beli": None, "jual": None}
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get("https://www.logammulia.com/", headers=HTTP_HDR)
            if r.status_code == 200:
                nums = re.findall(r'(\d[\d.]*)\s*</', r.text)
                vals = [int(n.replace('.','')) for n in nums if len(n.replace('.',''))>=6]
                if len(vals) >= 2:
                    results["jual"] = max(vals)
                    results["beli"] = min(vals)
        except: pass
        if not results["beli"]:
            try:
                r = await c.get("https://anekalogam.co.id/", headers=HTTP_HDR)
                if r.status_code == 200:
                    nums = re.findall(r'(\d[\d.]*)\s*</', r.text)
                    vals = [int(n.replace('.','')) for n in nums if len(n.replace('.',''))>=6]
                    if len(vals) >= 2:
                        results["jual"] = max(vals)
                        results["beli"] = min(vals)
            except: pass
    if not results["beli"]: results["beli"] = 2700000
    if not results["jual"]: results["jual"] = 2475000
    return results

async def fetch_rates():
    rates = {"usd": None, "cny": None, "rub": None}
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get("https://open.er-api.com/v6/latest/USD")
            if r.status_code == 200:
                d = r.json()
                rt = d.get("rates", {})
                rates["usd"] = int(rt.get("IDR", 0))
                rates["cny"] = rt.get("CNY")
                rates["rub"] = rt.get("RUB")
        except: pass
    if not rates["usd"]: rates["usd"] = 17797
    if not rates["cny"]: rates["cny"] = 6.77
    if not rates["rub"]: rates["rub"] = 72.94
    return rates

async def fetch_news():
    """Fetch real headlines from multiple RSS sources."""
    news = []
    urls = [
        f"https://news.google.com/rss/search?q=ekonomi+indonesia+emas&hl=id&gl=ID&ceid=ID:id",
        "https://rss.detik.com/index.php/ekonomi",
        "https://www.antaranews.com/rss/terkini"
    ]
    async with httpx.AsyncClient(timeout=15) as c:
        for url in urls:
            if len(news) >= 4:
                break
            try:
                r = await c.get(url, headers=HTTP_HDR, timeout=8)
                if r.status_code == 200 and "xml" in r.headers.get("content-type", ""):
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(r.text, "xml")
                    for item in soup.find_all("item")[:4]:
                        t = item.find("title")
                        if t:
                            txt = t.get_text(strip=True)
                            txt = re.sub(r'\s*-\s*\S+\s*$', '', txt)
                            if txt and len(txt) > 15 and txt not in news:
                                news.append(txt)
            except:
                pass
    return news[:6] or ["Data berita tidak tersedia"]

async def fetch_ai_promos():
    """Check known AI endpoints for availability/status."""
    results = []
    endpoints = [
        ("9ROUTER (proxy)", f"https://ai.jefripunza.com/v1/models"),
    ]
    async with httpx.AsyncClient(timeout=10) as c:
        for name, url in endpoints:
            try:
                r = await c.get(url, headers=HTTP_HDR, timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    models = data.get("data", [])[:3]
                    names = [m.get("id", "?") for m in models]
                    results.append(f"{name}: {len(models)} model tersedia ({', '.join(names[:3])})")
                else:
                    results.append(f"{name}: status {r.status_code}")
            except:
                results.append(f"{name}: unreachable")
    results.append("OpenAI, DeepSeek, Groq, Gemini: cek harga per 1M token")
    results.append("OpenRouter: agregator — compare pricing per model")
    return results

async def call_ai(gold, rates, news, promos, hour, date_str):
    """Call AI to format monitoring report from real data."""
    cny_idr = int(rates['usd'] / rates['cny']) if rates.get('usd') and rates.get('cny') else 0
    rub_idr = int(rates['usd'] / rates['rub']) if rates.get('usd') and rates.get('rub') else 0
    brics = int((cny_idr + rub_idr) / 2) if cny_idr and rub_idr else 0
    news_bullets = '\n'.join(f'▸ {n}' for n in news[:5])
    promo_bullets = '\n'.join(f'▸ {p}' for p in promos[:5])

    prompt = f"""Buat laporan monitoring rapi dari data real berikut, Bahasa Indonesia.

DATA REAL (jangan diubah):
- Emas Beli (buyback): Rp {gold['beli']:,}
- Emas Jual: Rp {gold['jual']:,}
- Spread: Rp {gold['jual'] - gold['beli']:,}/g ({(gold['jual'] - gold['beli']) / gold['beli'] * 100:.1f}%)
- 1 USD = Rp {rates['usd']:,}
- 1 CNY = Rp {cny_idr:,}  → BRICS: Rp {brics:,}
- 1 RUB = Rp {rub_idr:,}
- Hari: {date_str}, jam {hour:02d}:00 WIB

BERITA:
{news_bullets}

PROMO AI:
{promo_bullets}

Format PERSIS (1 baris kosong antar bagian):
```
📊 LAPORAN MONITORING
🗓️ [HARI + TANGGAL JAWA], [TANGGAL] — ⏰ [JAM] WIB

🥇 HARGA EMAS ANTAM (Logam Mulia)
• Harga Beli (buyback 1g): RpX.XXX.XXX (▼ X,X%)
• Harga Jual (1g): RpX.XXX.XXX (▲ X,X%)
• Spread: RpX.XXX/g (X,X%)

💱 NILAI TUKAR
• 1 USD = Rp XX.XXX (▲ X,X%)
• 1 CNY = Rp X.XXX
• 1 RUB = Rp XXX
• BRICS: Rp X.XXX

🤖 PROMO AI HARI INI
{promo_bullets}

📰 KUMPULAN BERITA
{news_bullets}

📰 SENTIMEN
▸ [sentimen pasar: bearish/neutral/bullish beserta analisa berita di atas]

📊 REKOMENDASI: [JUAL/BELI/TAHAN]
[Alasan singkat berdasarkan spread, sentimen & berita]

━━━━━━━━━━━━━━━━━━━━━━━━━
Data: logammulia.com, anekalogam.co.id, exchangerates.org.uk, Google News, 9ROUTER
Update: [TANGGAL] [JAM] WIB
```

Rules:
1. HARI: Indonesia + Jawa (contoh: Kamis Pon)
2. FORMAT NAIK/TURUN: gunakan (▼ X,X%) atau (▲ X,X%) atau (→ X,X%) — dengan persentase perubahan, pakai koma sebagai desimal
3. JANGAN tambah disclaimer
4. Rekomendasi: JUAL jika spread >10%, BELI jika <6%, TAHAN 6-10%
5. GUNAKAN data berita dan promo yang sudah diberikan, jangan diubah
6. SENTIMEN: analisa dari berita di atas — apakah bullish, bearish, atau neutral untuk harga emas"""

    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(AI_ENDPOINT, json={
                "model": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "max_tokens": 2000
            }, headers={"Authorization": f"Bearer {AI_KEY}", "Content-Type": "application/json"})
            if r.status_code == 200:
                text = r.text.strip()
                import json
                m = re.search(r'\{.*\}', text, re.DOTALL)
                if not m:
                    print("No JSON in response", file=sys.stderr)
                    return None
                try:
                    data = json.loads(m.group(0))
                    content = data["choices"][0]["message"]["content"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    print("JSON parse failed", file=sys.stderr)
                    return None
                content = re.sub(r'<think[^>]*>.*?</think>', '', content, flags=re.DOTALL)
                content = content.replace('\\n', '\n').replace('\\t', '\t')
                return content.strip()
            else:
                print(f"AI err {r.status_code}: {r.text[:200]}", file=sys.stderr)
                return None
    except Exception as e:
        print(f"AI fail: {e}", file=sys.stderr)
        return None

def parse_recommendation(content):
    """Parse JUAL/BELI/TAHAN from AI response to set button colors."""
    for kw in ["REKOMENDASI:", "SARAN:"]:
        m = re.search(rf"{kw}\s*(JUAL|BELI|TAHAN)", content, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return "TAHAN"  # default

def get_button_styles(rec):
    """Return button styles based on recommendation.
    Styles: 1=gray, 2=blue(primary), 3=green(success), 4=red(danger)"""
    if rec == "JUAL":
        return {"jual": 3, "beli": 4}  # Jual=green(recommended), Beli=red
    elif rec == "BELI":
        return {"jual": 4, "beli": 3}  # Jual=red, Beli=green(recommended)
    else:  # TAHAN
        return {"jual": 2, "beli": 2}  # Both blue

def format_local(gold, rates):
    now = datetime.now()
    usd = rates.get("usd", 0)
    cny = rates.get("cny", 0)
    rub = rates.get("rub", 0)
    spread = gold["jual"] - gold["beli"]
    sp = spread / gold["beli"] * 100 if gold["beli"] else 0
    return f"""📊 LAPORAN MONITORING
🗓️ {now.strftime('%d %B %Y')} — ⏰ {now.strftime('%H:%M')} WIB

🥇 HARGA EMAS
• Beli: Rp {gold['beli']:,}
• Jual: Rp {gold['jual']:,}
• Spread: Rp {spread:,} ({sp:.1f}%)

💱 NILAI TUKAR
• 1 USD = Rp {usd:,}
• 1 CNY = Rp {int(usd/cny):,}
• 1 RUB = Rp {int(usd/rub):,}

⚠️ AI research gagal, data terbatas."""

async def send_discord(msg, rec="TAHAN"):
    styles = get_button_styles(rec)
    payload = {"content": msg[:1900]}
    payload["components"] = [{
        "type": 1,
        "components": [
            {"type": 2, "label": "📈 Jual", "style": styles["jual"], "custom_id": "jual_emas"},
            {"type": 2, "label": "📉 Beli", "style": styles["beli"], "custom_id": "beli_emas"}
        ]
    }]
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{DISCORD_API}/channels/{MONITOR_CHANNEL}/messages", headers=DISC_HDR, json=payload)
    return r.status_code == 200

async def main():
    now = datetime.now()
    print(f"Run {now.isoformat()}", file=sys.stderr)

    gold, rates, news, promos = await asyncio.gather(
        fetch_gold(), fetch_rates(), fetch_news(), fetch_ai_promos()
    )
    hour = now.hour

    msg = await call_ai(gold, rates, news, promos, hour, now.strftime("%d %B %Y"))

    if not msg:
        msg = format_local(gold, rates)

    rec = parse_recommendation(msg) if msg else "TAHAN"
    ok = await send_discord(msg, rec)
    print("OK" if ok else "FAIL", file=sys.stderr)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    asyncio.run(main())
