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

async def call_ai(gold, rates, hour, day_name, date_str):
    """Call AI to research + format monitoring report."""
    prompt = f"""Buat laporan monitoring emas dan nilai tukar untuk trader Indonesia.

Data:
- Emas Antam: Beli Rp {gold['beli']:,}, Jual Rp {gold['jual']:,}
- USD/IDR: {rates['usd']:,}
- CNY/USD: {rates['cny']:.2f}
- RUB/USD: {rates['rub']:.2f}
- Hari: {day_name}, {date_str}, jam {hour:02d}:00 WIB

Format laporan (gunakan tepat):
📊 LAPORAN MONITORING
🗓️ [HARI], [TANGGAL] — ⏰ [JAM] WIB
━━━━━━━━━━━━━━━━━━━━━━━━━
🥇 HARGA EMAS ANTAM
• Harga Beli (buyback 1g): RpX.XXX.XXX (▼/▲ pergerakan)
• Harga Jual (1g): RpX.XXX.XXX (▼/▲ pergerakan)
• Spread: RpX.XXX/g (X.X%)
💱 NILAI TUKAR
• 1 USD = Rp XX.XXX (▲/▼ X,XX%)
• 1 CNY = Rp X.XXX
• 1 RUB = Rp XXX
🤖 PROMO AI HARI INI
▸ [Provider] — [detail promo, riset dari internet]
📰 SENTIMEN
▸ [berita ekonomi/emas terkini]
📊 SARAN: [JUAL/BELI/TAHAN] — [alasan 1-2 kalimat]
⚠️ Disclaimer: Bukan saran keuangan.
━━━━━━━━━━━━━━━━━━━━━━━━━

Rules:
1. Riset promo AI dari internet — hanya provider yg ada promo
2. Cek berita ekonomi/emas dari Google News ID
3. Beri saran JUAL/BELI/TAHAN dengan alasan berdasarkan spread & sentimen
4. Gunakan ▼ untuk turun, ▲ untuk naik"""

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
                # Parse JSON response safely
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

async def send_discord(msg):
    payload = {"content": msg[:1900]}
    payload["components"] = [{
        "type": 1,
        "components": [
            {"type": 2, "label": "📈 Jual", "style": 1, "custom_id": "jual_emas"},
            {"type": 2, "label": "📉 Beli", "style": 1, "custom_id": "beli_emas"}
        ]
    }]
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{DISCORD_API}/channels/{MONITOR_CHANNEL}/messages", headers=DISC_HDR, json=payload)
    return r.status_code == 200

async def main():
    now = datetime.now()
    print(f"Run {now.isoformat()}", file=sys.stderr)

    gold, rates = await asyncio.gather(fetch_gold(), fetch_rates())
    hour = now.hour

    msg = await call_ai(gold, rates, hour, now.strftime("%A"), now.strftime("%d %B %Y"))

    if not msg:
        msg = format_local(gold, rates)

    ok = await send_discord(msg)
    print("OK" if ok else "FAIL", file=sys.stderr)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    asyncio.run(main())
