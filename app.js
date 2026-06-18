import 'dotenv/config';
import express from 'express';
import { InteractionResponseType, InteractionType, verifyKeyMiddleware } from 'discord-interactions';
import { spawn } from 'child_process';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const app = express();
const PORT = process.env.PORT || 8899;
const GUILD_ID = process.env.GUILD_ID || '1516963494636027924';
const CHANNEL_MANAGEMENT = '1516965584296874156';
const AI_RESPONSE_CHANNEL = '1517028462437859412';

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
  // Prefer AI_API_KEY from env; fallback to hex-encoded key (backward compat)
  const keyHex = '736b2d626533663633653930396265656666312d6a35376b656f2d6537623432636532';
  const fallbackKey = Buffer.from(keyHex, 'hex').toString();
  // Sanity: env key must look valid (starts with sk-, >= 20 chars) or fall back
  const envKey = process.env.AI_API_KEY || process.env['9ROUTER_API_KEY'] || '';
  const key = (envKey.startsWith('sk-') && envKey.length >= 20) ? envKey : fallbackKey;
  return {
    apiKey: key,
    baseUrl: (process.env.AI_BASE_URL || 'https://ai.jefripunza.com/v1').replace(/\/+$/, ''),
    model: process.env.AI_MODEL || 'agent',
    timeout: parseInt(process.env.AI_TIMEOUT || '30000', 10)
  };
}
const AI = loadAI();

async function callAI(prompt, sysMsg = '', retries = 2) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), AI.timeout);
  try {
    const r = await fetch(AI.baseUrl + '/chat/completions', {
      method: 'POST',
      signal: ac.signal,
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
    clearTimeout(timer);
    const text = await r.text();
    if (!r.ok) {
      const detail = text.includes('{') ? (JSON.parse(text).error?.message || text) : text;
      // Skip retry on 4xx (auth/quota — permanent), retry on 5xx (transient)
      if (r.status >= 400 && r.status < 500 && retries > 0) {
        logError('ai', `AI ${r.status} (permanent, not retrying): ${detail.slice(0, 200)}`);
        return `❌ AI Error: service unavailable (${r.status})`;
      }
      throw new Error(`AI ${r.status}: ${detail.slice(0, 200)}`);
    }
    return JSON.parse(text).choices?.[0]?.message?.content?.trim() || '';
  } catch (e) {
    clearTimeout(timer);
    if (e.name === 'AbortError') {
      logError('ai', 'AI timeout (' + AI.timeout + 'ms)');
      return `❌ AI Error: timeout after ${AI.timeout/1000}s`;
    }
    // Retry 5xx / network errors with exponential backoff
    if (retries > 0 && !e.message.includes('(permanent') && !e.message.match(/AI 4[0-9][0-9]:/)) {
      const delay = Math.pow(2, 3 - retries) * 1000; // 1s, 2s, 4s
      logError('ai', `AI error, retry in ${delay}ms (${retries} left): ${e.message.slice(0, 120)}`);
      await new Promise(resolve => setTimeout(resolve, delay));
      return callAI(prompt, sysMsg, retries - 1);
    }
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
  const { type, data, guild_id, channel_id, member, token: intToken, message } = req.body;

  // PING
  if (type === InteractionType.PING) return res.send({ type: InteractionResponseType.PONG });

  // ═══ BUTTON CLICK (MESSAGE_COMPONENT) ═══
  if (type === InteractionType.MESSAGE_COMPONENT) {
    const customId = data?.custom_id || '';
    const msgId = message?.id;

    // Disable buttons immediately
    if (msgId) {
      try { await tool('disable_buttons', { guild_id, channel_id: channel_id, message_id: msgId }); } catch (_) {}
    }

    const userMention = '<@' + (member?.user?.id || '') + '>';

    if (customId === 'jual_emas' || customId === 'beli_emas') {
      const arah = customId === 'jual_emas' ? 'JUAL' : 'BELI';
      res.send({ type: 5 }); // Defer

      // Get current message content for context
      let msgContent = message?.content || '';

      // AI analysis
      const analysis = await callAI(
        `User clicked "${arah}" on monitoring message.\n\nMessage context:\n${msgContent.slice(0, 1500)}\n\nAnalyze: what happens if user ${arah.toLowerCase()}s emas today based on the data? Include profit/loss estimation, risks, and recommendation. Respond in Bahasa Indonesia with markdown.`,
        'You are a gold trading analyst. Give realistic analysis with profit/loss scenarios.'
      );

      // Send analysis to ai-response channel
      const report = `**${userMention} klik ${arah}**\n\n${analysis || 'Analisa tidak tersedia.'}`;
      await tool('send_message', { guild_id, name_or_id: AI_RESPONSE_CHANNEL, content: report.slice(0, 1900) });

      // Reply to the interaction
      await patchMsg(intToken, '✅ **' + arah + '** dianalisa.\nHasil analisa di <#' + AI_RESPONSE_CHANNEL + '>.');
      return;
    }

    // Unknown button
    return res.send({ type: 4, data: { content: '❌ Tombol tidak dikenal.', flags: 64 } });
  }

  // ═══ SLASH COMMANDS ═══
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
    // Only in channel-management
    if (channel_id !== CHANNEL_MANAGEMENT) {
      return res.send({ type: 4, data: { content: '❌ /prompt hanya bisa digunakan di <#' + CHANNEL_MANAGEMENT + '>' } });
    }

    const prompt = options?.find(o => o.name === 'prompt')?.value;
    if (!prompt) return res.send({ type: 4, data: { content: '❌ Prompt wajib diisi.' } });
    res.send({ type: 5 });

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

    await patchMsg(intToken, '🧠 **Menganalisa...**');

    // ── Step 1: Regex-based intent & action detection ──
    const lower = prompt.toLowerCase();
    let actionCmd = null;
    let actionResult = null;

    // 1a. Rename channel: "ubah nama X jadi Y" / "rename X ke Y" / "ganti nama X menjadi Y"
    const renameMatch = lower.match(/(?:ubah|rename|ganti)\s+(?:nama\s+)?(?:channel\s+)?#?(\S[^jadi]+?)\s*(?:jadi|ke|menjadi|to)\s+(.+)/i);
    if (renameMatch) {
      actionCmd = 'RENAME';
      const chName = renameMatch[1].trim();
      const newName = renameMatch[2].trim();
      await patchMsg(intToken, '🔧 **Merename channel...**');
      const r = await tool('rename_channel', { guild_id, name_or_id: chName, new_name: newName });
      const d = typeof r === 'object' ? r : {};
      if (d.id) actionResult = `✅ Channel \`#${chName}\` di-rename ke \`#${d.name}\`.`;
      else actionResult = '❌ Gagal rename: ' + (d.error || 'channel tidak ditemukan');
    }

    // 1b. Create channel: "buat channel X" / "create channel X"
    if (!actionCmd) {
      const cm = lower.match(/(?:buatkan|buat|create|bikin|bangun)\s+(?:channel|saluran)?\s*(?:"([^"]+)"|([a-z0-9\s-]+))/i);
      if (cm) {
        actionCmd = 'CREATE';
        const raw = (cm[1] || cm[2] || '').trim();
        await patchMsg(intToken, '🔧 **Membuat channel...**');
        const ch = await tool('create_channel', { guild_id, name: raw });
        if (ch.id) actionResult = '✅ Channel <#' + ch.id + '> dibuat.';
        else actionResult = '❌ Gagal: ' + (ch.error || 'unknown');
      }
    }

    // 1c. Delete channel: "hapus channel X" / "delete channel X"
    if (!actionCmd) {
      const dm = lower.match(/(?:hapus|delete|remove)\s+(?:channel\s+)?#?(\S[^jadi]+?)(?:\s*$|$)/i);
      if (dm) {
        actionCmd = 'DELETE';
        const raw = dm[1].trim().replace(/["']/g, '');
        await patchMsg(intToken, '🔧 **Menghapus channel...**');
        const r = await tool('delete_channel', { guild_id, name_or_id: raw });
        const d = typeof r === 'object' ? r : {};
        if (d.success) actionResult = '✅ Channel dihapus.';
        else actionResult = '❌ Gagal: ' + (d.error || 'channel tidak ditemukan');
      }
    }

    // 1d. Send message: "kirim pesan ke #X: isi"
    if (!actionCmd) {
      const sm = lower.match(/(?:kirim|send)\s+(?:pesan\s+)?(?:ke\s+)?#?(\S+?)\s*[:\-]\s*(.+)/i);
      if (sm) {
        actionCmd = 'SEND';
        const target = sm[1].trim();
        const msg = sm[2].trim();
        await patchMsg(intToken, '🔧 **Mengirim pesan...**');
        const r = await tool('send_message', { guild_id, name_or_id: target, content: msg });
        const d = typeof r === 'object' ? r : {};
        actionResult = d.success ? '✅ Pesan terkirim.' : '❌ Gagal: ' + (d.error || 'channel tidak ditemukan');
      }
    }

    // 1e. Request about monitoring / cron / analysis → QUERY
    const queryPatterns = /^(harga|promo|analisa|monitoring|cron|coba|test|apa|kapan|siapa|berapa|aktivitas|sejarah|riwayat)/i;
    const isQuery = !actionCmd && (
      queryPatterns.test(lower.trim()) ||
      prompt.length < 15 ||
      /\?/.test(prompt) ||
      /promo|kebutuhan|emas|dollar|cron|jadwal/i.test(lower)
    );

    // ── Step 2: Execute or forward ──
    if (actionCmd) {
      // Action done directly above → report result
      const finalText = actionResult || '✅ Selesai.';
      await tool('send_message', { guild_id, name_or_id: AI_RESPONSE_CHANNEL, content: '**Prompt:** ' + prompt + '\n\n' + finalText });
      await patchMsg(intToken, '✅ **Selesai.**\n' + finalText);
    } else if (isQuery) {
      // Query → forward to Hermes
      await patchMsg(intToken, '📨 **Permintaan diterima.**\nResponse akan muncul di <#' + AI_RESPONSE_CHANNEL + '>.');
      await tool('send_message', { guild_id, name_or_id: AI_RESPONSE_CHANNEL, content: '**Prompt dari <@' + (member?.user?.id || '') + '>:**\n' + prompt });
    } else {
      // Unknown → use AI
      const aiAnswer = await callAI(prompt, `Kamu asisten Discord. Anda punya akses MCP tools: create_channel, delete_channel, rename_channel, send_message, set_topic.

Jika user meminta action Discord (buat/hapus/rename channel atau kirim pesan), balas dengan:
ACTION:CMD|...args...
CONTOH: "buat channel test" → "create_channel"
"rename X jadi Y" → "rename_channel"
"hapus X" → "delete_channel"
"kirim ke X: isi" → "send_message"

Jika pertanyaan biasa, jawab seperti asisten AI normal. Balas dalam Bahasa Indonesia.`);

      const fallbackResult = aiAnswer?.startsWith('ACTION:') ? null : aiAnswer;
      if (fallbackResult) {
        await tool('send_message', { guild_id, name_or_id: AI_RESPONSE_CHANNEL, content: '**Prompt:** ' + prompt + '\n\n' + fallbackResult });
        await patchMsg(intToken, '✅ **Selesai.**\nResponse di <#' + AI_RESPONSE_CHANNEL + '>.');
      } else {
        await patchMsg(intToken, '✅ **Selesai.** (tidak ada action yang dikenali)');
      }
    }
    return;
  }

  console.error(`unknown: ${name}`);
  return res.status(400).json({ error: 'unknown command' });
});

app.listen(PORT, () => console.log('Listening on port', PORT));
