import 'dotenv/config';
import express from 'express';
import { InteractionResponseType, InteractionType, verifyKeyMiddleware } from 'discord-interactions';
import { spawn } from 'child_process';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const app = express();
const PORT = process.env.PORT || 8899;
const GUILD_ID = process.env.GUILD_ID || '1516963494636027924';

// ─── MCP Client ───
let mcpClient = null;
async function connectMCP() {
  const transport = new StdioClientTransport({
    command: process.env.HOME + '/.hermes/hermes-agent/venv/bin/python',
    args: [process.env.HOME + '/workspace/discord-mcp-server/discord_mcp.py']
  });
  mcpClient = new Client({ name: 'discord-bot-backend', version: '1.0.0' });
  await mcpClient.connect(transport);
  console.log('MCP connected');
}

async function tool(name, args = {}) {
  if (!mcpClient) throw new Error('MCP not connected');
  const r = await mcpClient.callTool({ name, arguments: { guild_id: GUILD_ID, ...args } });
  const text = r?.content?.[0]?.text || '{}';
  try { return JSON.parse(text); } catch { return { text }; }
}

connectMCP().catch(e => console.error('MCP init:', e.message));

// ─── Error logger ───
const { appendFileSync, existsSync, mkdirSync } = await import('fs');
const { join } = await import('path');
const LOG_DIR = process.env.HOME + '/.hermes/logs';
const ERR_LOG = join(LOG_DIR, 'discord-bot-error.log');
function logError(ctx, err) {
  const e = `[${new Date().toISOString()}] [${ctx}] ${err?.message || err}\n`;
  try {
    if (!existsSync(LOG_DIR)) mkdirSync(LOG_DIR, { recursive: true });
    appendFileSync(ERR_LOG, e);
  } catch (_) {}
  console.error(`[ERR] ${ctx}: ${(err?.message || err).slice(0, 200)}`);
}

// ─── AI (via 9ROUTER) ───
function loadAI() {
  const keyHex = '736b2d626533663633653930396265656666312d6a35376b656f2d6537623432636532';
  return {
    apiKey: Buffer.from(keyHex, 'hex').toString(),
    baseUrl: (process.env.AI_BASE_URL || 'https://ai.jefripunza.com/v1').replace(/\/+$/, ''),
    model: 'agent'
  };
}
const AI = loadAI();

async function callAI(prompt, sysMsg = '') {
  try {
    const r = await fetch(AI.baseUrl + '/chat/completions', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + AI.apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: AI.model,
        messages: [
          { role: 'system', content: sysMsg || 'Kamu asisten Discord.' },
          { role: 'user', content: prompt }
        ],
        max_tokens: 1024, temperature: 0.7, stream: false
      })
    });
    const text = await r.text();
    if (!r.ok) throw new Error(`AI ${r.status}: ${(text.includes('{') ? JSON.parse(text).error?.message || text : text).slice(0, 200)}`);
    return JSON.parse(text).choices?.[0]?.message?.content?.trim() || '';
  } catch (e) {
    logError('ai', e);
    return `❌ AI Error: ${e.message.slice(0, 250)}`;
  }
}

// ─── Helpers ───
async function patchMsg(intToken, content) {
  const url = `https://discord.com/api/v10/webhooks/${process.env.CLIENT_ID}/${encodeURIComponent(intToken)}/messages/@original`;
  await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: content.slice(0, 1900) }) });
}

// ─── Interactions ───
app.post('/interactions', verifyKeyMiddleware(process.env.PUBLIC_KEY), async (req, res) => {
  const { type, data, guild_id, channel_id, member, token: intToken } = req.body;
  if (type === InteractionType.PING) return res.send({ type: InteractionResponseType.PONG });
  if (type !== InteractionType.APPLICATION_COMMAND) return res.status(400).json({ error: 'unknown type' });

  const { name, options } = data;
  console.log(`[/${name}] from ${member?.user?.username}`);

  // ═══ /create ═══
  if (name === 'create') {
    const nama = options?.find(o => o.name === 'nama')?.value;
    if (!nama) return res.send({ type: 4, data: { content: '❌ Nama wajib diisi.' } });
    try {
      const ch = await tool('create_channel', { guild_id, name: nama });
      if (ch.error) return res.send({ type: 4, data: { content: `❌ ${ch.error}` } });
      return res.send({ type: 4, data: { content: '✅ <#' + ch.id + '> dibuat (`#' + ch.name + '`)' } });
    } catch (e) { logError('create', e); return res.send({ type: 4, data: { content: `❌ ${e.message.slice(0, 120)}` } }); }
  }

  // ═══ /edit ═══
  if (name === 'edit') {
    const chId = options?.find(o => o.name === 'channel')?.value;
    const instruksi = options?.find(o => o.name === 'instruksi')?.value;
    if (!chId || !instruksi) return res.send({ type: 4, data: { content: '❌ Pilih channel + instruksi.' } });
    res.send({ type: 5 });
    await patchMsg(intToken, '🧠 **Menganalisa instruksi...**');

    let result = null;
    const low = instruksi.toLowerCase();
    if (low.includes('hapus') || low.includes('delete')) {
      result = await tool('delete_channel', { guild_id, name_or_id: chId });
      result = result.success ? '✅ Channel dihapus.' : '❌ Gagal hapus channel.';
    } else if (low.includes('rename') || low.includes('ganti')) {
      const m = low.match(/(?:rename|ganti)\s+(?:nama)?\s*(?:jadi|ke|menjadi)?\s+(\S.+)/);
      const newName = m ? m[1].trim() : null;
      if (newName) {
        result = await tool('rename_channel', { guild_id, name_or_id: chId, new_name: newName });
        result = result.id ? `✅ Channel di-rename ke \`#${result.name}\`` : '❌ Gagal rename.';
      } else result = '❌ Nama baru tidak ditemukan.';
    } else if (low.includes('topik') || low.includes('topic')) {
      const m = low.match(/(?:topik|topic|set|jadi|ke)\s+(.+)/);
      const topic = m ? m[1].trim() : instruksi;
      result = await tool('set_topic', { guild_id, name_or_id: chId, topic });
      result = result.success ? '✅ Topic channel diupdate.' : '❌ Gagal set topic.';
    } else {
      // Use AI for complex instructions
      const ai = await callAI(
        `On Discord channel ${chId}, user says: ${instruksi}. Respond with what you did or ask for clarification.`,
        'Kamu asisten Discord. Jika hapus/delete → balas "HAPUS|id". Jika rename → balas "RENAME|id|nama". Jika topic → balas "TOPIC|id|teks". Jika tidak jelas → balas dengan penjelasan saja.'
      );
      result = ai;
    }
    await patchMsg(intToken, result || '✅ Instruksi diterima.');
    return;
  }

  // ═══ /prompt ═══
  if (name === 'prompt') {
    const prompt = options?.find(o => o.name === 'prompt')?.value;
    if (!prompt) return res.send({ type: 4, data: { content: '❌ Prompt wajib diisi.' } });
    res.send({ type: 5 });

    // Use AI + MCP to process
    const sysMsg = `Kamu adalah bot Discord yang BISA mengeksekusi perintah. Anda punya akses ke tools Discord:
- create_channel(name)
- delete_channel(name_or_id)
- rename_channel(name_or_id, new_name)
- set_topic(name_or_id, topic)
- send_message(name_or_id, content)
- list_channels()
- get_channel(name_or_id)

Guild ID: ${guild_id}

Untuk perintah seperti "buat channel X", "hapus channel Y", "rename channel Z jadi ABC", "kirim pesan ke #general halo", langsung lakukan action pakai tools di atas dan balas dengan hasilnya.

Untuk pertanyaan atau perintah lain, jawab seperti biasa.`;

    await patchMsg(intToken, '🧠 **Menganalisa permintaan...**');

    // Try AI first
    let answer = await callAI(prompt, sysMsg);

    // Retry once on error
    if (answer.startsWith('❌')) {
      answer = await callAI(prompt, sysMsg);
    }

    // If AI says it can't or empty, use regex fallback
    if (!answer || answer.startsWith('❌') || answer.includes('tidak dapat') || answer.includes('tidak punya') || answer.includes("can't") || answer.includes("don't have")) {
      const lower = prompt.toLowerCase();

      // Create channel
      const cm = lower.match(/(?:buatkan|buat|create|bikin|bangun)\s+(?:channel|saluran)?\s*(?:"([^"]+)"|([a-z0-9\s-]+))/i);
      if (cm) {
        await patchMsg(intToken, '🔧 **Membuat channel...**');
        const raw = (cm[1] || cm[2] || '').trim();
        const ch = await tool('create_channel', { guild_id, name: raw });
        answer = ch.id ? `✅ Channel <#${ch.id}> (\`#${ch.name}\`) dibuat.` : `❌ Gagal: ${ch.error || 'unknown'}`;
      }

      // Delete channel
      if (!answer || answer.startsWith('❌') || answer === '') {
        const dm = lower.match(/(?:hapus|delete|remove)\s+(?:channel)?\s*(?:"([^"]+)"|([a-z0-9\s-]+))/i);
        if (dm) {
          await patchMsg(intToken, '🔧 **Menghapus channel...**');
          const raw = (dm[1] || dm[2] || '').trim();
          answer = await tool('delete_channel', { guild_id, name_or_id: raw });
          answer = answer.success ? '✅ Channel dihapus.' : `❌ Gagal: ${answer.error || 'channel tidak ditemukan'}`;
        }
      }
    }

    if (!answer) answer = '✅ Selesai.';
    await patchMsg(intToken, answer);
    return;
  }

  console.error(`unknown: ${name}`);
  return res.status(400).json({ error: 'unknown command' });
});

app.listen(PORT, () => console.log('Listening on port', PORT));
