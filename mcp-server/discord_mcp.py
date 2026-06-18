"""
Discord MCP Server — exposes Discord REST API as MCP tools.
Stdio transport. Works with Hermes Gateway and Node.js backend.
"""
import asyncio, json, os, re, sys, yaml
import httpx
from mcp.server.fastmcp import FastMCP

# ── Token ──
def load_token():
    # 1. creds.json backup
    p = os.path.expanduser("~/workspace/discord-backend.py.bak/creds.json")
    if os.path.exists(p):
        with open(p) as f:
            d = json.load(f)
        if d.get("DISCORD_BOT_TOKEN"):
            return d["DISCORD_BOT_TOKEN"]
    # 2. Hermes config yaml
    p = os.path.expanduser("~/.hermes/config.yaml")
    if os.path.exists(p):
        with open(p) as f:
            cfg = yaml.safe_load(f)
        # Try discord token
        for k in ["discord", "plugins"]:
            if isinstance(cfg.get(k), dict) and "token" in cfg[k]:
                return cfg[k]["token"]
    # 3. Env
    if tok := os.environ.get("DISCORD_TOKEN", ""):
        return tok
    return ""

TOKEN = os.environ.get("DISCORD_TOKEN") or load_token()
HEADERS = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}
API = "https://discord.com/api/v10"

mcp = FastMCP("discord")

# ── Helpers ──
async def _get(guild_id: str) -> list:
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{API}/guilds/{guild_id}/channels", headers=HEADERS)
        return r.json() if r.status_code == 200 else []

async def _resolve(guild_id: str, name_or_id: str) -> str | None:
    """Resolve channel name → ID. Return None if not found."""
    if re.match(r"^\d{17,19}$", name_or_id):
        return name_or_id
    channels = await _get(guild_id)
    q = name_or_id.lower().replace("-", " ")
    for ch in channels:
        cn = (ch.get("name", "") or "").lower().replace("-", " ")
        if cn == q or cn.endswith(q) or q in cn:
            return ch["id"]
    return None

def _slug(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9\s-]", "", s.lower()).replace(" ", "-")).strip("-")

# ── TOOLS ──
@mcp.tool()
async def list_channels(guild_id: str) -> str:
    """List all channels in a guild. Returns JSON list with id, name, type."""
    channels = await _get(guild_id)
    out = []
    for ch in channels:
        out.append({"id": ch["id"], "name": ch["name"], "type": ch.get("type", 0)})
    return json.dumps(out, indent=2)

@mcp.tool()
async def create_channel(guild_id: str, name: str, type: int = 0, parent_id: str = "1516963495499792475") -> str:
    """Create a Discord channel. Returns channel JSON."""
    slug = _slug(name)
    if len(slug) < 2:
        return json.dumps({"error": "Name too short after sanitization"})
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{API}/guilds/{guild_id}/channels", headers=HEADERS,
                         json={"name": slug, "type": type, "parent_id": parent_id})
        if r.status_code in (200, 201):
            return json.dumps(r.json())
        return json.dumps({"error": r.text})

@mcp.tool()
async def delete_channel(guild_id: str, name_or_id: str) -> str:
    """Delete a Discord channel by name or ID."""
    target = await _resolve(guild_id, name_or_id)
    if not target:
        return json.dumps({"error": "Channel not found"})
    async with httpx.AsyncClient() as c:
        r = await c.delete(f"{API}/channels/{target}", headers=HEADERS)
        if r.status_code == 204:
            return json.dumps({"success": True, "id": target})
        return json.dumps({"error": r.text})

@mcp.tool()
async def rename_channel(guild_id: str, name_or_id: str, new_name: str) -> str:
    """Rename a Discord channel by name or ID."""
    target = await _resolve(guild_id, name_or_id)
    if not target:
        return json.dumps({"error": "Channel not found"})
    slug = _slug(new_name)
    if len(slug) < 2:
        return json.dumps({"error": "New name too short"})
    async with httpx.AsyncClient() as c:
        r = await c.patch(f"{API}/channels/{target}", headers=HEADERS,
                          json={"name": slug})
        if r.status_code == 200:
            return json.dumps(r.json())
        return json.dumps({"error": r.text})

@mcp.tool()
async def set_topic(guild_id: str, name_or_id: str, topic: str) -> str:
    """Set channel topic by name or ID."""
    target = await _resolve(guild_id, name_or_id)
    if not target:
        return json.dumps({"error": "Channel not found"})
    async with httpx.AsyncClient() as c:
        r = await c.patch(f"{API}/channels/{target}", headers=HEADERS,
                          json={"topic": topic[:1024]})
        if r.status_code == 200:
            return json.dumps({"success": True, "topic": topic[:1024]})
        return json.dumps({"error": r.text})

@mcp.tool()
async def send_message(guild_id: str, name_or_id: str, content: str) -> str:
    """Send a message to a channel by name or ID."""
    target = await _resolve(guild_id, name_or_id)
    if not target:
        return json.dumps({"error": "Channel not found"})
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{API}/channels/{target}/messages", headers=HEADERS,
                         json={"content": content[:1900]})
        if r.status_code == 200:
            return json.dumps({"success": True, "message_id": r.json().get("id")})
        return json.dumps({"error": r.text})

@mcp.tool()
async def get_channel(guild_id: str, name_or_id: str) -> str:
    """Get channel info by name or ID."""
    target = await _resolve(guild_id, name_or_id)
    if not target:
        return json.dumps({"error": "Channel not found"})
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{API}/channels/{target}", headers=HEADERS)
        if r.status_code == 200:
            return json.dumps(r.json())
        return json.dumps({"error": r.text})

if __name__ == "__main__":
    if not TOKEN or len(TOKEN) < 10:
        print("MCP: no valid Discord token found", file=sys.stderr)
        sys.exit(1)
    mcp.run(transport="stdio")
