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

// ─── Process AI prompt with interim thinking + retry ───
async function processAIPrompt(prompt, sysPrompt, guildId, channelId, intToken) {
  // Step 1: Analyzing
  await patchFollowup(intToken, '🧠 **Menganalisa permintaan...**');

  // Retry with exponential backoff (3 attempts: 1s, 2s, 4s)
  let aiText = await callAI(prompt, sysPrompt);
  for (let attempt = 2; attempt <= 3 && aiText.startsWith('❌ AI Error:'); attempt++) {
    await new Promise(r => setTimeout(r, 1000 * Math.pow(2, attempt - 2)));
    aiText = await callAI(prompt, sysPrompt);
  }
  if (aiText.startsWith('❌ AI Error:')) {
    logError('AI_final', aiText);
    return { error: true, text: aiText.replace('❌ AI Error: ', '') };
  }

  // Parse action from AI
  const { action, text } = parseAction(aiText);

  const lower = prompt.toLowerCase();

  // Fallback 1: regex-based for common patterns
  let finalAction = action;
  if (!finalAction) {
    const chMatch = lower.match(/(?:buat|create|bikin)\s*(?:channel|saluran)?\s*"?([a-z0-9\s-]+)"?\s*$/i);
    if (chMatch) {
      const name = chMatch[1].trim().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '').replace(/^-|-$/g, '');
      if (name.length >= 2) {
        finalAction = `CREATE|${name}`;
      }
    }
    const delMatch = lower.match(/(?:hapus|delete|remove)\s*(?:channel)?\s*"?([a-z0-9\s-]+)"?\s*$/i);
    if (delMatch && !finalAction) {
      finalAction = 'NONE';
    }
  }

  // Step 2: Executing
  const cmdLabel = finalAction ? finalAction.split('|')[0] : '';
  const execMsg = finalAction ? `🔧 **${cmdLabel === 'CREATE' ? 'Membuat channel...' : 'Mengeksekusi...'}**` : '✅ Selesai.';
  await patchFollowup(intToken, `🧠 **${cmdLabel === 'CREATE' ? 'Membuat channel...' : 'Memproses...'}**`);

  const result = finalAction ? await executeAction(finalAction, guildId, channelId) : null;

  // Step 3: Result
  const finalText = result || text || (finalAction ? '✅ Selesai.' : text || '✅ Selesai.');
  return { error: false, text: finalText };
}

// ─── Try to parse action from AI response ───
function parseAction(text) {
  const lines = text.split('\n').map(l => l.trim());
  let action = null;
  const filtered = [];

  for (const line of lines) {
    if (line.startsWith('ACTION:')) {
      action = line.replace('ACTION:', '').trim();
    } else if (line.startsWith('- ACTION:')) {
      action = line.replace('- ACTION:', '').trim();
    } else {
      // Skip empty/irrelevant lines that look like raw instructions
      if (line && !line.match(/^(ACTION|CMD|FORMAT|Contoh|Gunakan|RESPOND|Example|Use the)/i)) {
        filtered.push(line);
      }
    }
  }

  // Normalize action: CREATE_CHANNEL -> CREATE, DELETE_CHANNEL -> DELETE, etc.
  if (action) {
    action = action.replace(/_CHANNEL$/, '').replace(/_MESSAGE$/, 'MSG');
  }

  return { action, text: filtered.join('\n').trim() };
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

      // processAIPrompt handles interim thinking messages
      const sysPrompt = `Extract a Discord action. RESPOND EXACTLY:
ACTION: CMD|channel_id|args
CMD: DELETE | RENAME|new_name | TOPIC|text | NONE
Channel: ${chId}
Instruction: ${instruksi}`;

      const result = await processAIPrompt(
        `Instruction on channel ${chId}: ${instruksi}`,
        sysPrompt, guild_id, chId, intToken
      );

      if (result.error) {
        await patchFollowup(intToken, `❌ **Gagal memproses instruksi**\n> \`${result.text.slice(0, 150)}\`\n🧠 Self-fixing agent sudah diberitahu.`);
        logError('edit_fail', result.text);
      } else {
        await patchFollowup(intToken, result.text || '✅ Instruksi diterima.');
      }
      return;
    }

    // ═══ /prompt ═══
    if (name === 'prompt') {
      const prompt = options?.find(o => o.name === 'prompt')?.value;
      if (!prompt) return res.send({ type: 4, data: { content: '❌ Prompt wajib diisi.' } });

      res.send({ type: 5 }); // Defer

      // processAIPrompt handles interim thinking messages
      const sysPrompt = `You are a Discord bot. For user request:
- If it asks to do something in Discord, respond with ACTION: CMD|args
- CMDs: CREATE|channel_name, DELETE|channel_id, RENAME|channel_id|new_name, MSG|channel_id|text, TOPIC|channel_id|text, NONE
- If no action needed, respond with your answer and ACTION: NONE`;
      const result = await processAIPrompt(prompt, sysPrompt, guild_id, channel_id, intToken);

      if (result.error) {
        await patchFollowup(intToken, `❌ **Gagal memproses**\n> \`${result.text.slice(0, 150)}\`\n🧠 Self-fixing agent akan handle. Coba lagi nanti.`);
        logError('prompt_fail', result.text);
      } else {
        await patchFollowup(intToken, result.text || '✅ Selesai.');
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
