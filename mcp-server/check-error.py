#!/usr/bin/env python3
"""Check discord-bot-error log for new errors → send to #bot-error channel.
Silent exit if no new errors. Track offset via state file."""
import asyncio, json, os, sys
from datetime import datetime
import httpx

HOME = os.path.expanduser("~")
STATE_FILE = os.path.join(HOME, ".hermes/scripts/.monitor-error-state")
LOG_FILE = os.path.join(HOME, ".hermes/logs/discord-bot-error.log")
CHANNEL_ID = "1517063581835853895"  # bot-error

def load_token():
    p = os.path.join(HOME, "workspace/discord-backend.py.bak/creds.json")
    if os.path.exists(p):
        with open(p) as f:
            d = json.load(f)
        if d.get("DISCORD_BOT_TOKEN"):
            return d["DISCORD_BOT_TOKEN"]
    return os.environ.get("DISCORD_TOKEN", "")

BOT_KEY = load_token()
if not BOT_KEY or len(BOT_KEY) < 10:
    print("❌ No valid Discord token", file=sys.stderr)
    sys.exit(1)

DISC_HDR = {"Authorization": f"Bot {BOT_KEY}", "Content-Type": "application/json"}

async def send_discord(msg):
    payload = {"content": msg[:1900]}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages",
            headers=DISC_HDR, json=payload
        )
    return r.status_code == 200

async def main():
    # Read state (last byte offset)
    last_offset = 0
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                last_offset = int(f.read().strip() or "0")
        except:
            last_offset = 0

    # Read log file
    if not os.path.exists(LOG_FILE):
        # No log file = no error
        sys.exit(0)

    with open(LOG_FILE) as f:
        f.seek(last_offset)
        new_content = f.read()
        current_size = f.tell()

    # If no new content, silent exit
    stripped = new_content.strip()
    if not stripped:
        sys.exit(0)

    # New error found — format and send
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")
    lines = stripped.splitlines()
    # First 20 lines
    body = "\n".join(lines[:20])
    if len(lines) > 20:
        body += f"\n... (+{len(lines) - 20} baris)"

    msg = f"""🛑 **BOT ERROR DETECTED**
━━━━━━━━━━━━━━━━━━
⏰ {ts} WIB

```{body}```"""

    await send_discord(msg)
    print(f"Sent {len(lines)} lines to #bot-error", file=sys.stderr)

    # Update state
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write(str(current_size))

if __name__ == "__main__":
    asyncio.run(main())
