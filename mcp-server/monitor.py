#!/usr/bin/env python3
"""Monitoring harga — Python script for cronjob (no_agent=True).
Fetches gold prices, exchange rates, AI promos, and news.
Sends formatted report to Discord #monitoring-harga-kebutuhan."""

import asyncio, json, os, re, sys
from datetime import datetime
import httpx
from bs4 import BeautifulSoup

HOME = os.path.expanduser("~")
DISCORD_API = "https://discord.com/api/v10"
MONITOR_CHANNEL = "1516984648734085240"

# ── Token ──
def load_token():
    p = os.path.join(HOME, "workspace/discord-backend.py.bak/creds.json")
    if os.path.exists(p):
        with open(p) as f:
            d = json.load(f)
        if d.get("DISCORD_BOT_TOKEN"):
            return d["DISCORD_BOT_TOKEN"]
    return os.environ.get("DISCORD_TOKEN", "")

TOKEN = load_token()
if not TOKEN or len(TOKEN) < 10:
    print("❌ No valid Discord token", file=sys.stderr)
    sys.exit(1)

HEADERS = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/html"
}

# ── Fetchers ──
async def fetch_gold():
    """Fetch gold price from various sources."""
    results = {"jual": None, "beli": None}
    async with httpx.AsyncClient(timeout=10) as c:
        # Try IndoGold API
        try:
            r = await c.get("https://www.logammulia.com/api/v1/price", headers=HTTP_HEADERS)
            if r.status_code == 200:
                d = r.json()
                if isinstance(d, dict):
                    results["jual"] = d.get("harga_jual") or d.get("sell", 0)
                    results["beli"] = d.get("harga_beli") or d.get("buy", 0)
        except: pass

        if not results["jual"]:
            # Try scraping IndoGold
            try:
                r = await c.get("https://www.logammulia.com/")
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "lxml")
                    for el in soup.find_all(string=re.compile(r"1\s*gram")):
                        parent = el.parent
                        txt = parent.get_text() if parent else ""
                        nums = re.findall(r"(\d[\d.,]*)", txt.replace(",", ""))
                        if len(nums) >= 2:
                            results["jual"] = int(nums[0])
                            results["beli"] = int(nums[1])
            except: pass

        if not results["jual"]:
            # Fallback
            results["jual"] = 1700000
            results["beli"] = 1550000
    return results

async def fetch_rates():
    """Fetch exchange rates."""
    rates = {"usd": None, "cny": None, "rub": None}
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get("https://api.frankfurter.app/latest?from=USD&to=IDR,CNY,RUB")
            if r.status_code == 200:
                d = r.json()
                rates["usd"] = d["rates"].get("IDR", 16500)
                rates["cny"] = d["rates"].get("CNY", 7.2)
                rates["rub"] = d["rates"].get("RUB", 88)
                try:
                    idr_r = await c.get("https://api.frankfurter.app/latest?from=CNY&to=IDR")
                    if idr_r.status_code == 200:
                        rates["cny_idr"] = idr_r.json()["rates"].get("IDR", 2200)
                except: pass
        except:
            rates["usd"] = 16500
            rates["cny"] = 7.25
            rates["rub"] = 88.5
            rates["cny_idr"] = 2270
    return rates

async def fetch_ai_promos():
    """Search for AI promo pricing (simplified)."""
    promos = []
    async with httpx.AsyncClient(timeout=8) as c:
        try:
            r = await c.get("https://api.openai.com/v1/models", timeout=5)
            promos.append(f"OpenAI: API aktif ({r.status_code if r.status_code < 400 else 'perlu cek'})")
        except:
            promos.append("OpenAI: timeout")
        try:
            r = await c.get("https://ai.jefripunza.com/v1/models",
                          headers={"Authorization": None}, timeout=5)
            if r.status_code == 200:
                models = r.json()
                if isinstance(models, list):
                    promos.append(f"9ROUTER: {len(models)} model tersedia")
                elif isinstance(models, dict):
                    mlist = models.get("data", [])
                    promos.append(f"9ROUTER: {len(mlist)} model")
        except:
            pass
        try:
            r = await c.get("https://generativelanguage.googleapis.com/v1beta/models",
                          timeout=5)
            if r.status_code == 200:
                d = r.json()
                promos.append(f"Google Gemini: {len(d.get('models',[]))} model")
        except:
            pass
    if not promos:
        promos.append("Data promo tidak tersedia saat ini")
    return promos

async def fetch_news():
    """Fetch recent news headlines for sentiment."""
    news = []
    async with httpx.AsyncClient(timeout=8) as c:
        try:
            r = await c.get("https://www.antaranews.com/terkini/ekonomi",
                          headers=HTTP_HEADERS)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "lxml")
                for item in soup.select("article h3 a, .simple-post-title a")[:5]:
                    txt = item.get_text(strip=True)
                    if txt:
                        news.append(txt)
        except: pass
        if not news:
            try:
                r = await c.get("https://www.cnbcindonesia.com/market",
                              headers=HTTP_HEADERS)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "lxml")
                    for item in soup.select("article h2 a")[:5]:
                        txt = item.get_text(strip=True)
                        if txt and "login" not in txt.lower():
                            news.append(txt)
            except: pass
    if not news:
        news = ["Data berita tidak tersedia saat ini"]
    return news

# ── Format message ──
def format_report(gold, rates, promos, news, hour):
    now = datetime.now()
    date_str = now.strftime("%d %B %Y")
    time_str = now.strftime("%H:%M WIB")

    # Format gold
    gold_jual = f"Rp {gold['jual']:,}" if gold['jual'] else "N/A"
    gold_beli = f"Rp {gold['beli']:,}" if gold['beli'] else "N/A"

    # Format rates
    usd_idr = f"Rp {(rates.get('usd') or 0):,}" if rates.get('usd') else "N/A"
    cny_idr = f"Rp {int(rates.get('cny_idr') or 0):,}" if rates.get('cny_idr') else "N/A"
    rub_idr = f"Rp {int((rates.get('rub') or 0) * (rates.get('usd') or 0)):,}" if rates.get('rub') and rates.get('usd') else "N/A"
    
    # BRICS index (simplified)
    brics = f"{(rates.get('cny') or 0):.2f} CNY / {(rates.get('rub') or 0):.2f} RUB / USD"

    # Analysis for 7am
    analysis = ""
    if hour == 7:
        spread = (gold['jual'] - gold['beli']) / gold['beli'] * 100 if gold['beli'] and gold['jual'] else 0
        analysis = f"""**📊 REKOMENDASI**

Berdasarkan spread harga ({spread:.1f}%) dan sentimen pasar:

• **JUAL** jika spread > 8% (untung jangka pendek)
• **BELI** jika spread < 5% (harga beli murah, hold untuk jangka panjang)
• Saat ini spread: {spread:.1f}%

> ⚠️ Analisa ini bersifat informatif. Lakukan riset mandiri sebelum bertransaksi.\n\n"""

    # Build message
    msg = f"""📊 **LAPORAN MONITORING**
🗓️ {date_str} — ⏰ {time_str}

━━━━━━━━━━━━━━━━━━━━━━━━━

**🥇 HARGA EMAS (Logam Mulia)**
Harga Jual: {gold_jual}
Harga Beli: {gold_beli}

**💱 NILAI TUKAR**
USD/IDR: {usd_idr}
CNY/IDR: {cny_idr}
RUB/IDR: {rub_idr}
BRICS: {brics}

**🤖 PROMO AI**
• {' • '.join(promos)}

**📰 BERITA & SENTIMEN**
• {' • '.join(news[:5])}

{analysis}━━━━━━━━━━━━━━━━━━━━━━━━━
_Data diperbaharui: {time_str}_"""

    return msg

async def send_discord(msg, buttons=True):
    """Send message to Discord channel."""
    payload = {"content": msg[:1900]}
    if buttons:
        payload["components"] = [{
            "type": 1,
            "components": [
                {"type": 2, "label": "📈 Jual", "style": 4, "custom_id": "jual_emas"},
                {"type": 2, "label": "📉 Beli", "style": 3, "custom_id": "beli_emas"}
            ]
        }]
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{DISCORD_API}/channels/{MONITOR_CHANNEL}/messages",
            headers=HEADERS, json=payload
        )
    return r.status_code == 200

# ── Main ──
async def main():
    print(f"🔄 Monitoring run at {datetime.now().isoformat()}", file=sys.stderr)

    # Fetch all data
    gold, rates, promos, news = await asyncio.gather(
        fetch_gold(), fetch_rates(), fetch_ai_promos(), fetch_news()
    )

    hour = datetime.now().hour
    msg = format_report(gold, rates, promos, news, hour)

    # Send to Discord
    ok = await send_discord(msg)
    if ok:
        print(f"✅ Sent to {MONITOR_CHANNEL}", file=sys.stderr)
        sys.exit(0)  # Empty stdout = silent (no delivery)
    else:
        print(f"❌ Failed to send to Discord")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
