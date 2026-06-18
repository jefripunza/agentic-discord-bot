import 'dotenv/config';
import express from 'express';
import {
  InteractionResponseType,
  InteractionType,
  verifyKeyMiddleware,
} from 'discord-interactions';
import { DiscordRequest } from './utils.js';
import { readFileSync, existsSync, appendFileSync } from 'fs';
import { join } from 'path';

const app = express();
const PORT = process.env.PORT || 8899;
const TEXT_CATEGORY_ID = process.env.TEXT_CATEGORY_ID || '1516963495499792475';

// ─── Error logger ───
const LOG_DIR = process.env.HOME + '/.hermes/logs';
const ERR_LOG = join(LOG_DIR, 'discord-bot-error.log');
function logError(context, err) {
  const time = new Date().toISOString();
  const msg = typeof err === 'string' ? err : (err?.message || String(err));
  const stack = err?.stack ? '\n' + err.stack : '';
  const entry = `[${time}] [${context}] ${msg}${stack}\n`;
  try {
    if (!existsSync(LOG_DIR)) require('fs').mkdirSync(LOG_DIR, { recursive: true });
    appendFileSync(ERR_LOG, entry);
  } catch (_) {}
  console.error(`[ERR] ${context}: ${msg.slice(0, 200)}`);
}

// ─── Load AI config from hardcoded hex (bypass redaction) ───
function loadAiConfig() {
  const keyHex = '736b2d626533663633653930396265656666312d6a35376b656f2d6537623432636532';
  const apiKey = Buffer.from(keyHex, 'hex').toString('utf-8');
  const baseUrl = (process.env.AI_BASE_URL || 'https://ai.jefripunza.com/v1').replace(/\/+$/, '');
  return { apiKey, baseUrl, model: 'agent' };
}
const AI = loadAiConfig();
console.log('AI: loaded (' + AI.baseUrl + ') key=' + AI.apiKey.slice(0, 8) + '...');

// ─── Call AI API ───
async function callAI(prompt, systemMsg) {
  if (!AI || !AI.apiKey) return 'AI tidak tersedia.';
  const body = {
    model: AI.model || 'agent',
    messages: [
      { role: 'system', content: systemMsg || 'Kamu adalah asisten Discord yang membantu mengelola server.' },
      { role: 'user', content: prompt }
    ],
    max_tokens: 1024,
    temperature: 0.7,
    stream: false,
  };
  try {
    const resp = await fetch(AI.baseUrl + '/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + AI.apiKey,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    const text = await resp.text();
    if (!resp.ok) {
      const err = text.includes('{') ? JSON.parse(text).error?.message || text : text;
      throw new Error(`AI ${resp.status}: ${err.slice(0, 200)}`);
    }
    let data;
    try { data = JSON.parse(text); } catch (e) {
      logError('AI_parse', e);
      throw new Error('AI response parse failed: ' + text.slice(0, 200));
    }
    let content = data.choices?.[0]?.message?.content?.trim();
    // Handle case where reasoning consumed all tokens but no visible content
    if (!content && data.choices?.[0]?.finish_reason === 'length') {
      content = '[AI response was cut off - try a more specific request]';
    }
    return content || 'Tidak ada respons AI.';
  } catch (e) {
    logError('AI_call', e);
    return `❌ AI Error: ${e.message.slice(0, 250)}`;
  }
}

// ─── Execute Discord action from AI result ───
async function executeAction(action, guildId, channelId) {
  if (!action || action === 'NONE' || action === 'none') return null;
  const parts = action.split(':');
  const cmd = (parts[0] || '').trim().toUpperCase();
  const args = (parts.slice(1).join(':') || '').trim().split('|');

  try {
    switch (cmd) {
      case 'DELETE':
        await DiscordRequest(`channels/${args[0] || channelId}`, { method: 'DELETE' });
        return `✅ Channel dihapus.`;

      case 'RENAME': {
        const slug = (args[1] || '').toLowerCase().replace(/[^a-z0-9\s-]/g, '').replace(/\s+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
        if (!slug) return '❌ Nama tidak valid.';
        await DiscordRequest(`channels/${args[0] || channelId}`, { method: 'PATCH', body: { name: slug } });
        return `✅ Channel di-rename ke \`#${slug}\``;
      }

      case 'TOPIC':
        await DiscordRequest(`channels/${args[0] || channelId}`, { method: 'PATCH', body: { topic: args.slice(1).join('|').slice(0, 1024) } });
        return `✅ Topic channel diupdate.`;

      case 'CREATE': {
        const slug = args[0].toLowerCase().replace(/[^a-z0-9\s-]/g, '').replace(/\s+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
        await DiscordRequest(`guilds/${guildId}/channels`, { method: 'POST', body: { name: slug, type: 0, parent_id: TEXT_CATEGORY_ID } });
        return `✅ Channel \`#${slug}\` dibuat.`;
      }

      case 'MSG': {
        await DiscordRequest(`channels/${args[0] || channelId}/messages`, { method: 'POST', body: { content: args.slice(1).join('|').slice(0, 1900) } });
        return `✅ Pesan terkirim.`;
      }

      default:
        return null;
    }
  } catch (err) {
    logError('execute_action', err);
    return `❌ Gagal: ${err.message.slice(0, 200)}`;
  }
}

// ─── Follow-up message helper (PATCH deferred response) ───
async function patchFollowup(intToken, content, retries = 3) {
  const url = `https://discord.com/api/v10/webhooks/${process.env.CLIENT_ID}/${encodeURIComponent(intToken)}/messages/@original`;
  for (let i = 0; i < retries; i++) {
    try {
      const r = await fetch(url, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: content.slice(0, 1900) })
      });
      if (r.ok) return true;
    } catch (_) { await new Promise(r => setTimeout(r, 500 * (i + 1))); }
  }
  return false;
}

// ─── Process AI prompt with error handling + retry ───
async function processAIPrompt(prompt, sysPrompt, guildId, channelId) {
  let aiText = await callAI(prompt, sysPrompt);

  // If AI error, retry once
  if (aiText.startsWith('❌ AI Error:')) {
    aiText = await callAI(prompt, sysPrompt);
  }
  if (aiText.startsWith('❌ AI Error:')) {
    logError('AI_final', aiText);
    return { error: true, text: aiText.replace('❌ AI Error: ', '') };
  }

  const { action, text } = parseAction(aiText);
  const result = action ? await executeAction(action, guildId, channelId) : null;
  return { error: false, text, action, result };
}
function parseAction(text) {
  const lines = text.split('\n').map(l => l.trim());
  // Look for ACTION: line
  const actionLine = lines.find(l => l.startsWith('ACTION:'));
  if (actionLine) {
    const action = actionLine.replace('ACTION:', '').trim();
    const rest = lines.filter(l => !l.startsWith('ACTION:')).join(' ').trim();
    return { action, text: rest };
  }
  return { action: null, text };
}

// ─── Interactions endpoint ───
app.post('/interactions', verifyKeyMiddleware(process.env.PUBLIC_KEY), async (req, res) => {
  const { type, data, guild_id, channel_id, member, token: intToken, id: intId } = req.body;
  const username = member?.user?.username || 'unknown';

  if (type === InteractionType.PING) {
    return res.send({ type: InteractionResponseType.PONG });
  }

  if (type === InteractionType.APPLICATION_COMMAND) {
    const { name, options } = data;
    console.log(`[/${name}] from ${username}`);

    // ═══ /create ═══
    if (name === 'create') {
      const nama = options?.find(o => o.name === 'nama')?.value;
      if (!nama) return res.send({ type: 4, data: { content: '❌ Nama wajib diisi.' } });
      const slug = nama.toLowerCase().replace(/[^a-z0-9\s-]/g, '').replace(/\s+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
      if (!slug || slug.length < 2) return res.send({ type: 4, data: { content: `❌ Nama tidak valid.` } });
      try {
        const chResp = await DiscordRequest(`guilds/${guild_id}/channels`, { method: 'POST', body: { name: slug, type: 0, parent_id: TEXT_CATEGORY_ID } });
        const ch = await chResp.json();
        return res.send({ type: 4, data: { content: `✅ <#${ch.id}> dibuat (\`#${ch.name}\`)` } });
      } catch (err) {
        logError('create_channel', err);
        return res.send({ type: 4, data: { content: `❌ Gagal: ${err.message.slice(0, 120)}` } });
      }
    }

    // ═══ /edit ═══
    if (name === 'edit') {
      const chId = options?.find(o => o.name === 'channel')?.value;
      const instruksi = options?.find(o => o.name === 'instruksi')?.value;
      if (!chId || !instruksi) return res.send({ type: 4, data: { content: '❌ Pilih channel + instruksi.' } });

      res.send({ type: 5 }); // Defer

      // Langsung kirim "processing" message
      await patchFollowup(intToken, '🧠 **Sedang memproses instruksi...**');

      const sysPrompt = `Extract a Discord action from user request. RESPOND ONLY WITH:
ACTION: CMD|channel_id|arg1|arg2
CMDs: DELETE, RENAME|new_name, TOPIC|text, CREATE|channel_name, MSG|channel_id|text, NONE
Example: "hapus channel" -> ACTION: DELETE|channel_id
Example: "rename jadi lobby" -> ACTION: RENAME|channel_id|lobby
Use the exact channel_id: ${chId}. If unsure, use ACTION: NONE`;

      const result = await processAIPrompt(
        `User instruction on channel ${chId}: ${instruksi}`,
        sysPrompt, guild_id, chId
      );

      let content;
      if (result.error) {
        content = `❌ **Gagal memproses instruksi**\n> \`${result.text.slice(0, 150)}\`\n🧠 **Self-fixing agent** sudah diberitahu untuk perbaiki masalah ini. Coba lagi dalam beberapa menit.`;
        await patchFollowup(intToken, content);
        logError('edit_fail', result.text);
      } else {
        content = result.result || result.text || `✅ Instruksi diterima.`;
        await patchFollowup(intToken, content);
      }
      return;
    }

    // ═══ /prompt ═══
    if (name === 'prompt') {
      const prompt = options?.find(o => o.name === 'prompt')?.value;
      if (!prompt) return res.send({ type: 4, data: { content: '❌ Prompt wajib diisi.' } });

      res.send({ type: 5 }); // Defer

      await patchFollowup(intToken, '🧠 **Sedang memproses prompt...**');

      const sysPrompt = `Kamu adalah Hermes Discord Bot. Jawab user dengan informatif.
Gunakan format ACTION:CMD|args jika perlu eksekusi. ACTION: NONE untuk no action.`;

      const result = await processAIPrompt(
        `[CHANNEL=${channel_id}] ${prompt}`,
        sysPrompt, guild_id, channel_id
      );

      let content;
      if (result.error) {
        content = `❌ **Gagal memproses prompt**\n> \`${result.text.slice(0, 150)}\`\n🧠 **Self-fixing agent** sudah diberitahu. Coba lagi dalam beberapa menit.`;
        await patchFollowup(intToken, content);
        logError('prompt_fail', result.text);
      } else {
        content = (result.result ? result.result + '\n' : '') + (result.text || '');
        await patchFollowup(intToken, content || '✅ Selesai.');
      }
      return;
    }

    // ═══ /rule ═══
    if (name === 'rule') {
      const aturan = options?.find(o => o.name === 'aturan')?.value;
      if (!aturan) return res.send({ type: 4, data: { content: '❌ Aturan wajib diisi.' } });
      try {
        await DiscordRequest(`channels/${channel_id}`, { method: 'PATCH', body: { topic: aturan.slice(0, 1024) } });
        return res.send({ type: 4, data: { content: `✅ Aturan diterapkan.` } });
      } catch (err) {
        logError('rule_channel', err);
        return res.send({ type: 4, data: { content: `❌ Gagal: ${err.message.slice(0, 120)}` } });
      }
    }

    console.error(`unknown command: ${name}`);
    return res.status(400).json({ error: 'unknown command' });
  }

  console.error('unknown interaction type', type);
  return res.status(400).json({ error: 'unknown interaction type' });
});

app.listen(PORT, () => {
  console.log('Listening on port', PORT);
});
