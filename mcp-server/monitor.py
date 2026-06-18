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
    """Fetch gold price from IndoGold."""
    results = {"jual": None, "beli": None}
    async with httpx.AsyncClient(timeout=10) as c:
        # Source 1: indogold API
        try:
            r = await c.get("https://indogold.id/api/v2/prices/antam", headers=HTTP_HEADERS)
            if r.status_code == 200:
                d = r.json()
                if isinstance(d, dict):
                    g = d.get("prices", {}) or d
                    results["jual"] = int(g.get("buy", 0) or g.get("jual", 0) or 0)
                    results["beli"] = int(g.get("sell", 0) or g.get("beli", 0) or 0)
        except: pass

        # Source 2: try scraping logammulia
        if not results["jual"]:
            try:
                r = await c.get("https://www.logammulia.com/", headers=HTTP_HEADERS)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "lxml")
                    nums = []
                    for el in soup.find_all(text=re.compile(r"\b\d{1,3}(?:\.\d{3})*\b")):
                        n = re.sub(r"[^0-9]", "", el.strip())
                        if len(n) >= 6 and int(n) > 100000:
                            nums.append(int(n))
                    if len(nums) >= 2:
                        results["jual"] = max(nums)
                        results["beli"] = min(nums)
            except: pass

        # Hardcoded fallback
        if not results["jual"]: results["jual"] = 1700000
        if not results["beli"]: results["beli"] = 1550000
    return results

async def fetch_rates():
    """Fetch exchange rates with multiple fallbacks."""
    rates = {"usd": None, "cny": None, "rub": None, "cny_idr": None}
    async with httpx.AsyncClient(timeout=10) as c:
        # Source 1: exchangerate-api (more reliable)
        try:
            r = await c.get("https://open.er-api.com/v6/latest/USD")
            if r.status_code == 200:
                d = r.json()
                rt = d.get("rates", {})
                rates["usd"] = int(rt.get("IDR", 0))
                rates["cny"] = rt.get("CNY")
                rates["rub"] = rt.get("RUB")
        except: pass

        # CNY→IDR calculation
        if rates["usd"] and rates["cny"]:
            try:
                rates["cny_idr"] = int(rates["usd"] / rates["cny"])
            except: pass

        # Source 2: frankfurter fallback
        if not rates["usd"]:
            try:
                r = await c.get("https://api.frankfurter.app/latest?from=USD&to=IDR,CNY,RUB")
                if r.status_code == 200:
                    d = r.json()
                    rt = d.get("rates", {})
                    rates["usd"] = int(rt.get("IDR", 0))
                    rates["cny"] = rt.get("CNY")
                    rates["rub"] = rt.get("RUB")
            except: pass

    # Hardcoded fallback (typical values)
    if not rates["usd"]: rates["usd"] = 16500
    if not rates["cny"]: rates["cny"] = 7.25
    if not rates["rub"]: rates["rub"] = 88.5
    if not rates["cny_idr"]: rates["cny_idr"] = 2270
    return rates

async def fetch_ai_promos():
    """List available AI models from known endpoints."""
    promos = []
    async with httpx.AsyncClient(timeout=10) as c:
        # 1. Check 9ROUTER (our own endpoint)
        try:
            r = await c.get("https://ai.jefripunza.com/v1/models")
            if r.status_code == 200:
                data = r.json()
                models = data.get("data", data) if isinstance(data, dict) else data
                n = len(models) if isinstance(models, list) else 0
                if n > 0:
                    names = [m.get("id", "") for m in models[:5] if isinstance(m, dict)]
                    promos.append(f"9ROUTER: {n} model (~{', '.join(names[:2])}...)" if names else f"9ROUTER: {n} model")
                else:
                    promos.append("9ROUTER: online (model list empty)")
            else:
                promos.append(f"9ROUTER: status {r.status_code}")
        except Exception as e:
            promos.append(f"9ROUTER: error ({str(e)[:30]})")

        # 2. OpenAI
        try:
            r = await c.get("https://api.openai.com/v1/models",
                          headers={"Authorization": "Bearer none"})
            if r.status_code in (200, 401):
                promos.append("OpenAI: API online")
            else:
                promos.append(f"OpenAI: status {r.status_code}")
        except:
            promos.append("OpenAI: unreachable")

        # 3. Groq
        try:
            r = await c.get("https://api.groq.com/openai/v1/models",
                          headers={"Authorization": "Bearer none"})
            if r.status_code in (200, 401):
                promos.append("Groq: API online")
        except:
            promos.append("Groq: unreachable")

    if not promos:
        promos.append("Semua provider unreachable")
    return promos

async def fetch_news():
    """Fetch news headlines from multiple sources."""
    news = []
    async with httpx.AsyncClient(timeout=10) as c:
        # Source 1: Google News RSS (most reliable)
        try:
            r = await c.get("https://news.google.com/rss/search?q=ekonomi+indonesia&hl=id&gl=ID&ceid=ID:id",
                          headers=HTTP_HEADERS)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "xml")
                for item in soup.find_all("item")[:6]:
                    title = item.find("title")
                    if title:
                        t = title.get_text(strip=True)
                        # Remove source suffix like " - CNBC Indonesia"
                        t = re.sub(r'\s*-\s*\S+$', '', t)
                        if t and len(t) > 10:
                            news.append(t)
        except: pass

        # Source 2: Detik RSS
        if len(news) < 3:
            try:
                r = await c.get("https://rss.detik.com/index.php/ekonomi",
                              headers=HTTP_HEADERS)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "xml")
                    for item in soup.find_all("item")[:5]:
                        title = item.find("title")
                        if title:
                            t = title.get_text(strip=True)
                            if t and len(t) > 10 and not any(s in t.lower() for s in ['login','iklan']):
                                news.append(t)
            except: pass

        # Source 3: Antara RSS
        if len(news) < 3:
            try:
                r = await c.get("https://www.antaranews.com/rss/terkini",
                              headers=HTTP_HEADERS)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "xml")
                    for item in soup.find_all("item")[:5]:
                        title = item.find("title")
                        if title:
                            t = title.get_text(strip=True)
                            if t and len(t) > 10:
                                news.append(t.split(" - ")[0])
            except: pass

        # Source 4: CNBC scrape
        if len(news) < 3:
            try:
                r = await c.get("https://www.cnbcindonesia.com/market",
                              headers=HTTP_HEADERS)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "lxml")
                    for el in soup.select("article h2 a, .media-title a, a.title")[:5]:
                        txt = el.get_text(strip=True)
                        if txt and len(txt) > 10:
                            news.append(txt)
            except: pass

    if not news:
        news = ["Belum ada berita terbaru saat ini"]
    return news[:8]

# ── Format message ──
def format_report(gold, rates, promos, news, hour):
    now = datetime.now()
    date_str = now.strftime("%d %B %Y")
    time_str = now.strftime("%H:%M WIB")

    # Format gold
    gold_jual = f"Rp {gold['jual']:,}" if gold['jual'] else "N/A"
    gold_beli = f"Rp {gold['beli']:,}" if gold['beli'] else "N/A"

    # Format rates — per 1 unit to IDR
    usd = rates.get('usd') or 0
    cny = rates.get('cny') or 0
    rub = rates.get('rub') or 0

    usd_idr = f"1 USD = Rp {usd:,}" if usd else "USD: N/A"
    cny_idr = f"1 CNY = Rp {int(usd / cny):,}" if usd and cny else "CNY: N/A"
    rub_idr = f"1 RUB = Rp {int(usd / rub):,}" if usd and rub else "RUB: N/A"
    
    # BRICS — individual currencies
    brics_lines = []
    if usd and cny: brics_lines.append(f"CNY: Rp {int(usd / cny):,}")
    if usd and rub: brics_lines.append(f"RUB: Rp {int(usd / rub):,}")
    brics = " | ".join(brics_lines) if brics_lines else "BRICS: N/A"

    # Analysis for 7am
    analysis = ""
    if hour == 7:
        spread = (gold.get('jual', 0) - gold.get('beli', 0)) / gold.get('beli', 1) * 100 if gold.get('beli') else 0
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
• Harga Jual: Rp {gold['jual']:,}
• Harga Beli: Rp {gold['beli']:,}

**💱 NILAI TUKAR (per 1 unit → IDR)**
{usd_idr}
{cny_idr}
{rub_idr}
• BRICS: {brics}

**🤖 STATUS PROVIDER AI**
{chr(10).join('• ' + p for p in promos)}

**📰 BERITA & SENTIMEN**
{chr(10).join('• ' + n for n in (news[:5] or ['Belum tersedia']))}

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
