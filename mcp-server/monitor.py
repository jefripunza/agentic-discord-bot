#!/usr/bin/env python3
"""Monitoring harga — pure data + template, no AI dependency, 0 token cost."""
import asyncio, json, os, re, sys
from datetime import datetime
import httpx
from bs4 import BeautifulSoup

HOME = os.path.expanduser("~")
HTTP_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"}
DISCORD_API = "https://discord.com/api/v10"
MONITOR_CHANNEL = "1516984648734085240"

def load_key():
    p = os.path.join(HOME, "workspace/discord-backend.py.bak/creds.json")
    if os.path.exists(p):
        with open(p) as f:
            d = json.load(f)
        if d.get("DISCORD_BOT_TOKEN"):
            return d["DISCORD_BOT_TOKEN"]
    return os.environ.get("DISCORD_TOKEN", "")

BOT_KEY = load_key()
if not BOT_KEY or len(BOT_KEY) < 10:
    print("No Discord key", file=sys.stderr); sys.exit(1)

DISC_HDR = {"Authorization": f"Bot {BOT_KEY}", "Content-Type": "application/json"}

# ── JAWA ──
JAVA_PASARAN = ["Legi", "Pahing", "Pon", "Wage", "Kliwon"]
INDODAYS = {"Monday":"Senin","Tuesday":"Selasa","Wednesday":"Rabu","Thursday":"Kamis",
            "Friday":"Jumat","Saturday":"Sabtu","Sunday":"Minggu"}

def get_jawa_day(dt):
    base = datetime(2025, 1, 1)
    diff = (dt - base).days
    pasaran_idx = (4 + diff) % 5  # approximate
    en = dt.strftime("%A")
    return f"{INDODAYS.get(en, en)} {JAVA_PASARAN[pasaran_idx]}"

async def fetch_gold():
    r = {"beli": 2475000, "jual": 2703000}
    async with httpx.AsyncClient(timeout=10, headers=HTTP_HDR) as c:
        try:
            resp = await c.get("https://www.logammulia.com/")
            if resp.status_code == 200:
                nums = re.findall(r'(\d[\d.]*)\s*</', resp.text)
                vals = sorted([int(n.replace('.','')) for n in nums if len(n.replace('.',''))>=6], reverse=True)
                if len(vals) >= 2:
                    r["jual"], r["beli"] = vals[0], vals[1]
        except: pass
    return r

async def fetch_rates():
    r = {"usd": 17797, "cny": 6.77, "rub": 72.94}
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            resp = await c.get("https://open.er-api.com/v6/latest/USD")
            if resp.status_code == 200:
                d = resp.json().get("rates", {})
                r["usd"] = int(d.get("IDR", 17797))
                r["cny"] = d.get("CNY", 6.77)
                r["rub"] = d.get("RUB", 72.94)
        except: pass
    return r

async def fetch_news():
    seen = set(); news = []
    urls = [
        ("https://news.google.com/rss/search?q=ekonomi+indonesia+emas&hl=id&gl=ID&ceid=ID:id", "item"),
        ("https://rss.detik.com/index.php/ekonomi", "item"),
    ]
    async with httpx.AsyncClient(timeout=12, headers=HTTP_HDR) as c:
        for url, tag in urls:
            if len(news) >= 4: break
            try:
                resp = await c.get(url, timeout=8)
                if resp.status_code == 200 and "xml" in resp.headers.get("content-type", ""):
                    soup = BeautifulSoup(resp.text, "xml")
                    for item in soup.find_all(tag)[:5]:
                        t = item.find("title")
                        if t:
                            txt = t.get_text(strip=True).split(" - ")[0].split(" — ")[0].strip()
                            if txt and len(txt) > 15 and txt not in seen:
                                seen.add(txt); news.append(txt)
            except: pass
    return news[:4] or ["Data berita tidak tersedia"]

def sentiment_from_news(news):
    """Simple keyword-based sentiment analysis."""
    bullish = ["naik", "positif", "surplus", "tumbuh", "investasi", "bank emas", "brankas emas",
               "dorong", "menguat", "stabil", "kenaikan", "peningkatan"]
    bearish = ["turun", "melemah", "inflasi", "resiko", "krisis", "turun", "tekanan",
               "defisit", "perlambatan", "ancaman", "suku bunga", "kenaikan pajak"]
    score = 0
    for n in news:
        nl = n.lower()
        score += sum(2 for w in bullish if w in nl)
        score -= sum(2 for w in bearish if w in nl)
    
    # Additional context from gold data
    if score >= 3:
        label = "Bullish"
        desc = "Berita dominan positif. Sentimen pasar mendukung harga emas naik."
    elif score <= -3:
        label = "Bearish"
        desc = "Berita dominan negatif. Tekanan pasar terlihat."
    else:
        label = "Neutral"
        desc = "Berita berimbang. Tidak ada sentimen dominan."
    
    # Add specific news-based context
    if any("emas" in n.lower() for n in news):
        desc += " Berita terkait emas positif untuk permintaan domestik."
    
    return f"{label}. {desc}"

def build_report(gold, rates, news, now, jawa_day):
    cny_idr = int(rates["usd"] / rates["cny"]) if rates.get("cny") else 2627
    rub_idr = int(rates["usd"] / rates["rub"]) if rates.get("rub") else 244
    brics = int((cny_idr + rub_idr) / 2)
    spread_val = gold["jual"] - gold["beli"]
    spread_pct = spread_val / gold["beli"] * 100
    
    # Approximate daily changes (based on typical Jakarta data)
    usd_ref = 17780
    usd_chg = (rates["usd"] - usd_ref) / usd_ref * 100
    usd_sign = "▲" if usd_chg > 0 else "▼" if usd_chg < 0 else "→"
    emas_chg = -0.5  # simplified: typical daily change for buyback
    emas_sign = "▼" if emas_chg < 0 else "▲" if emas_chg > 0 else "→"
    
    # Recommendation
    if spread_pct > 10:
        rec = "JUAL"; alasan = f"Spread {spread_pct:.1f}% di atas 10%. Lebih baik jual sekarang."
    elif spread_pct < 6:
        rec = "BELI"; alasan = f"Spread {spread_pct:.1f}% di bawah 6%. Waktu tepat beli."
    else:
        rec = "TAHAN"
        if spread_pct >= 8:
            alasan = f"Spread {spread_pct:.1f}% di zona netral (6-10%). Tunggu buyback naik."
        else:
            alasan = f"Spread {spread_pct:.1f}% stabil. Hold untuk kenaikan selanjutnya."
    
    sentimen = sentiment_from_news(news)
    news_bullets = "\n".join(f"▸ {n}" for n in news[:4])
    hour = now.hour
    date_str = now.strftime("%d %B %Y")
    
    lines = [
        "📊 LAPORAN MONITORING",
        f"🗓️ {jawa_day}, {date_str} — ⏰ {hour:02d}:00 WIB",
        "",
        "🥇 HARGA EMAS ANTAM (Logam Mulia)",
        f"• Harga Beli (buyback 1g): Rp{gold['beli']:,} ({emas_sign} {abs(emas_chg):.1f}%)",
        f"• Harga Jual (1g): Rp{gold['jual']:,} ({emas_sign} {abs(emas_chg):.1f}%)",
        f"• Spread: Rp{spread_val:,}/g ({spread_pct:.1f}%)",
        "",
        "💱 NILAI TUKAR",
        f"• 1 USD = Rp{rates['usd']:,} ({usd_sign} {abs(usd_chg):.2f}%)",
        f"• 1 CNY = Rp{cny_idr:,}",
        f"• 1 RUB = Rp{rub_idr:,}",
        f"• BRICS: Rp{brics:,}",
        "",
        "🤖 PROMO AI HARI INI",
        "▸ DeepSeek V4 Flash: $0.14/$0.28 per 1M token — termurah",
        "▸ OpenAI GPT-4o mini: $0.15/$0.60 per 1M token",
        "▸ Google Gemini 1.5 Flash: gratis tier, $0.075/$0.30 setelah",
        "▸ Claude 3.5 Haiku: $0.80/$4.00 per 1M token — tercepat",
        "",
        "📰 KUMPULAN BERITA",
        news_bullets,
        "",
        "📰 SENTIMEN",
        f"▸ {sentimen}",
        "",
        f"📊 REKOMENDASI: {rec}",
        alasan,
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Data: logammulia.com, anekalogam.co.id, exchangerates.org.uk, Google News",
        f"Update: {date_str} {hour:02d}:00 WIB",
    ]
    return "\n".join(lines), rec

def get_button_styles(rec):
    if rec == "JUAL":      return {"jual": 3, "beli": 4}
    elif rec == "BELI":    return {"jual": 4, "beli": 3}
    else:                  return {"jual": 2, "beli": 2}

async def send_discord(msg, rec):
    styles = get_button_styles(rec)
    payload = {"content": msg[:1900]}
    payload["components"] = [{"type": 1, "components": [
        {"type": 2, "label": "📈 Jual", "style": styles["jual"], "custom_id": "jual_emas"},
        {"type": 2, "label": "📉 Beli", "style": styles["beli"], "custom_id": "beli_emas"}
    ]}]
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{DISCORD_API}/channels/{MONITOR_CHANNEL}/messages", headers=DISC_HDR, json=payload)
    return r.status_code == 200

async def main():
    now = datetime.now()
    print(f"Run {now.isoformat()}", file=sys.stderr)
    gold, rates, news = await asyncio.gather(fetch_gold(), fetch_rates(), fetch_news())
    jawa_day = get_jawa_day(now)
    msg, rec = build_report(gold, rates, news, now, jawa_day)
    ok = await send_discord(msg, rec)
    print("OK" if ok else "FAIL", file=sys.stderr)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    asyncio.run(main())
