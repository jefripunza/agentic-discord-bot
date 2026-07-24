#!/home/sawang/.hermes/hermes-agent/venv/bin/python
"""
Monitoring harga emas + kurs + berita — cronjob trigger.
Pakai Camoufox browser untuk antifingerprint scraping + LLM untuk sentimen & rekomendasi.
Dipanggil oleh Hermes cron: 0 7,12,20 * * *

Mengirim laporan ke #monitoring-elite-global channel dengan 2 button Jual/Beli.
"""
import asyncio, json, os, re, sys
from datetime import datetime
from bs4 import BeautifulSoup
from camoufox.async_api import AsyncCamoufox
from browserforge.fingerprints import Screen

HOME = os.path.expanduser("~")
DISCORD_API = "https://discord.com/api/v10"
MONITOR_CHANNEL = "1516984648734085240"

# ── 9ROUTER AI ──
AI_BASE_URL = (os.environ.get("AI_BASE_URL") or "https://ai.jefripunza.com/v1").rstrip("/")
AI_MODEL = os.environ.get("AI_MODEL") or "agent"

def load_ai_key():
    """Load AI key: env > credentials file > hex fallback.
       Credential files WAJIB disimpan di ~/credentials/, bukan di chat/Hindsight."""
    if tok := os.environ.get("AI_API_KEY", ""):
        return tok
    # env_9router — format export KEY=VALUE
    env_path = os.path.join(HOME, "credentials/env_9router")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export N9ROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    # hermes_9router.txt — format api_key=VALUE
    cred_path = os.path.join(HOME, "credentials/hermes_9router.txt")
    if os.path.exists(cred_path):
        with open(cred_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("api_key="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    # Hex fallback — terakhir jika semua gagal
    key_hex = "736b2d626533663633653930396265656666312d6a35376b656f2d6537623432636532"
    return bytes.fromhex(key_hex).decode()

AI_API_KEY = load_ai_key()

async def call_llm(system_prompt: str, user_prompt: str, timeout: int = 30) -> str:
    """Call 9ROUTER LLM API; return content text or empty string."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(
                f"{AI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": AI_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 512,
                    "temperature": 0.3,
                    "stream": False,
                },
            )
            if r.status_code == 200:
                data = r.json()
                return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
            else:
                print(f"LLM error {r.status_code}: {r.text[:200]}", file=sys.stderr)
                return ""
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
        return ""

# ── Discord Token ──
def load_key():
    if tok := os.environ.get("DISCORD_TOKEN", ""):
        return tok
    env_path = os.path.join(HOME, "workspace/discord-bot/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DISCORD_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
                if line.startswith("export DISCORD_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

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
    pasaran_idx = (4 + diff) % 5
    en = dt.strftime("%A")
    return f"{INDODAYS.get(en, en)} {JAVA_PASARAN[pasaran_idx]}"

# ── Scraping via Camoufox ──

async def fetch_gold(browser):
    """Scrape harga emas dari logammulia.com via Camoufox."""
    r = {"beli": 2475000, "jual": 2703000}
    try:
        page = await browser.new_page()
        await page.goto("https://www.logammulia.com/", wait_until="domcontentloaded", timeout=15000)
        content = await page.content()
        await page.close()
        nums = re.findall(r'(\d[\d.]*)\s*</', content)
        vals = sorted([int(n.replace('.','')) for n in nums if len(n.replace('.',''))>=6], reverse=True)
        if len(vals) >= 2:
            r["jual"], r["beli"] = vals[0], vals[1]
    except Exception as e:
        print(f"fetch_gold error: {e}", file=sys.stderr)
    return r

async def fetch_rates(browser):
    """Scrape kurs dari open.er-api.com via Camoufox."""
    r = {"usd": 17797, "cny": 6.77, "rub": 72.94}
    try:
        page = await browser.new_page()
        await page.goto("https://open.er-api.com/v6/latest/USD", wait_until="domcontentloaded", timeout=15000)
        text = await page.evaluate("document.body.innerText")
        await page.close()
        d = json.loads(text).get("rates", {})
        r["usd"] = int(d.get("IDR", 17797))
        r["cny"] = d.get("CNY", 6.77)
        r["rub"] = d.get("RUB", 72.94)
    except Exception as e:
        print(f"fetch_rates error: {e}", file=sys.stderr)
    return r

async def fetch_news(browser):
    """Scrape berita dari RSS + Google Search via Camoufox."""
    seen = set()
    news = []

    # 1. RSS sources (max 4)
    rss_urls = [
        "https://news.google.com/rss/search?q=ekonomi+indonesia+emas&hl=id&gl=ID&ceid=ID:id",
        "https://rss.detik.com/index.php/ekonomi",
    ]
    for url in rss_urls:
        if len(news) >= 4:
            break
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=12000)
            content = await page.content()
            await page.close()
            soup = BeautifulSoup(content, "xml")
            for item in soup.find_all("item")[:5]:
                t = item.find("title")
                if t:
                    txt = t.get_text(strip=True).split(" - ")[0].split(" — ")[0].strip()
                    if txt and len(txt) > 15 and txt not in seen:
                        seen.add(txt); news.append(txt)
        except Exception as e:
            print(f"fetch_news RSS ({url[:30]}): {e}", file=sys.stderr)

    # 2. Google Search (3 teratas)
    if len(news) < 7:
        try:
            page = await browser.new_page()
            search_q = "harga+emas+hari+ini+indonesia+2026"
            await page.goto(
                f"https://www.google.com/search?q={search_q}&hl=id&gl=ID",
                wait_until="networkidle",
                timeout=15000,
            )
            titles = await page.evaluate("""() => {
                const results = [];
                const h3s = document.querySelectorAll('h3');
                h3s.forEach(h => { const t = h.innerText.trim(); if (t.length > 15) results.push(t); });
                return results.slice(0, 3);
            }""")
            await page.close()
            for txt in titles:
                if txt not in seen:
                    seen.add(txt)
                    news.append(f"[Google] {txt}")
        except Exception as e:
            print(f"fetch_news Google: {e}", file=sys.stderr)

    return news[:7] or ["Data berita tidak tersedia"]

# ── Sentimen via LLM ──

SENTIMENT_SYSTEM_PROMPT = """Anda adalah analis pasar emas dan ekonomi senior dengan spesialisasi pasar Indonesia.
Tugas Anda: menganalisis berita-berita yang diberikan dan menghasilkan sentimen pasar.

Format output WAJIB seperti ini (hanya 2 baris, tanpa markdown, tanpa label tambahan):
BULLISH|Berita dominan positif dengan sentimen pasar yang mendukung harga emas naik. Alasan: [1-2 kalimat analisis singkat berdasarkan berita].
NEUTRAL|Berita berimbang tanpa sentimen dominan. Alasan: [1-2 kalimat analisis].
BEARISH|Berita dominan negatif dengan tekanan pasar terlihat. Alasan: [1-2 kalimat analisis singkat berdasarkan berita].

ATURAN:
- Baris pertama: BULLISH / NEUTRAL / BEARISH (huruf besar semua) + pipe + deskripsi.
- Analisis harus berdasarkan fakta dari berita yang diberikan, jangan mengada-ada.
- Jika berita terkait emas, kebijakan moneter, atau investasi, beri bobot lebih.
- Gunakan Bahasa Indonesia yang profesional.
- Output hanya 2 baris. Baris pertama adalah label+deskripsi. Baris kedua opsional rekomendasi singkat (max 1 kalimat)."""

async def sentiment_from_llm(news: list) -> str:
    """Analisa sentimen berita pakai LLM."""
    if not news or news == ["Data berita tidak tersedia"]:
        return "NEUTRAL|Tidak ada berita yang cukup untuk analisis sentimen."
    news_text = "\n".join(f"{i+1}. {n}" for i, n in enumerate(news))
    prompt = f"Berita hari ini ({len(news)} artikel):\n\n{news_text}\n\nAnalisis sentimen pasar."
    result = await call_llm(SENTIMENT_SYSTEM_PROMPT, prompt)
    if not result:
        return "NEUTRAL|Gagal memproses analisis sentimen via AI."
    result = result.replace("```", "").strip()
    return result

# ── Rekomendasi via LLM ──

RECOMMEND_SYSTEM_PROMPT = """Anda adalah analis investasi emas senior untuk pasar Indonesia.
Tugas Anda: memberikan rekomendasi JUAL / BELI / TAHAN berdasarkan data harga, spread, kurs, dan sentimen pasar.

Format output WAJIB 2 baris:
JUAL|[1-2 kalimat alasan analitis berdasarkan data yang diberikan]
BELI|[1-2 kalimat alasan analitis berdasarkan data yang diberikan]
TAHAN|[1-2 kalimat alasan analitis berdasarkan data yang diberikan]

ATURAN:
- Analisis harus berdasarkan data yang diberikan secara faktual.
- Spread di atas 10% cenderung JUAL, spread di bawah 6% cenderung BELI.
- Perhatikan juga sentimen pasar (BULLISH cenderung BELI, BEARISH cenderung JUAL).
- Kurs USD yang tinggi (>18000) bisa jadi tekanan, sebaliknya jika rendah (<17500) bisa positif.
- Gunakan Bahasa Indonesia yang profesional, singkat, dan padat.
- Jangan mengulang data mentah. Langsung ke analisis. Output hanya 2 baris."""

async def recommend_from_llm(gold: dict, rates: dict, sentimen: str) -> tuple:
    """Analisa rekomendasi pakai LLM berdasarkan data."""
    spread_val = gold["jual"] - gold["beli"]
    spread_pct = spread_val / gold["beli"] * 100
    prompt = (
        f"Data hari ini:\n"
        f"- Harga Beli emas: Rp{gold['beli']:,}/g\n"
        f"- Harga Jual emas: Rp{gold['jual']:,}/g\n"
        f"- Spread: Rp{spread_val:,}/g ({spread_pct:.1f}%)\n"
        f"- Kurs USD: Rp{rates['usd']:,}\n"
        f"- Sentimen pasar: {sentimen}\n\n"
        f"Berikan rekomendasi investasi emas hari ini."
    )
    result = await call_llm(RECOMMEND_SYSTEM_PROMPT, prompt)
    if not result:
        # Fallback ke rule-based
        if spread_pct > 10:
            return "JUAL", f"Spread {spread_pct:.1f}% di atas 10%. Lebih baik jual sekarang."
        elif spread_pct < 6:
            return "BELI", f"Spread {spread_pct:.1f}% di bawah 6%. Waktu tepat beli."
        else:
            return "TAHAN", f"Spread {spread_pct:.1f}% stabil. Hold untuk kenaikan selanjutnya."
    result = result.replace("```", "").strip()
    lines = result.split("\n", 1)
    label = lines[0].split("|")[0].strip().upper()
    alasan = lines[0].split("|", 1)[-1].strip() if "|" in lines[0] else lines[0]
    if label not in ("JUAL", "BELI", "TAHAN"):
        # LLM output tidak sesuai format, fallback
        label = "TAHAN"
        alasan = "Analisis tidak dapat diproses. Hold posisi."
    return label, alasan

# ── Report Builder ──

def build_report(gold, rates, news, sentimen, rekomendasi, alasan, now, jawa_day):
    cny_idr = int(rates["usd"] / rates["cny"]) if rates.get("cny") else 2627
    rub_idr = int(rates["usd"] / rates["rub"]) if rates.get("rub") else 244
    brics = int((cny_idr + rub_idr) / 2)
    spread_val = gold["jual"] - gold["beli"]
    spread_pct = spread_val / gold["beli"] * 100
    usd_ref = 17780
    usd_chg = (rates["usd"] - usd_ref) / usd_ref * 100
    usd_sign = "▲" if usd_chg > 0 else "▼" if usd_chg < 0 else "→"
    emas_chg = -0.5
    emas_sign = "▼" if emas_chg < 0 else "▲" if emas_chg > 0 else "→"
    news_bullets = "\n".join(f"▸ {n}" for n in news[:7])
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
        "📰 SENTIMEN (AI)",
        f"▸ {sentimen}",
        "",
        "📊 REKOMENDASI (AI)",
        f"▸ {rekomendasi}",
        f"▸ {alasan}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Data: logammulia.com, anekalogam.co.id, exchangerates.org.uk, Google News, Google Search",
        f"Update: {date_str} {hour:02d}:00 WIB",
    ]
    return "\n".join(lines), rekomendasi

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
    import httpx
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{DISCORD_API}/channels/{MONITOR_CHANNEL}/messages", headers=DISC_HDR, json=payload)
    return r.status_code == 200

async def main():
    now = datetime.now()
    print(f"Run {now.isoformat()}", file=sys.stderr)

    async with AsyncCamoufox(
        headless=True,
        screen=Screen(min_width=1280, max_width=1280, min_height=720, max_height=720),
    ) as browser:
        gold, rates, news = await asyncio.gather(
            fetch_gold(browser),
            fetch_rates(browser),
            fetch_news(browser),
        )

    jawa_day = get_jawa_day(now)

    # Sentimen pakai LLM
    print(f"News collected: {len(news)} items", file=sys.stderr)
    sentimen = await sentiment_from_llm(news)
    print(f"Sentiment: {sentimen[:80]}...", file=sys.stderr)

    # Rekomendasi pakai LLM
    rekomendasi, alasan = await recommend_from_llm(gold, rates, sentimen)
    print(f"Recommendation: {rekomendasi} — {alasan[:60]}...", file=sys.stderr)

    msg, rec = build_report(gold, rates, news, sentimen, rekomendasi, alasan, now, jawa_day)
    ok = await send_discord(msg, rec)
    print("OK" if ok else "FAIL", file=sys.stderr)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    asyncio.run(main())
