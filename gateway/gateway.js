/**
 * Gateway WhatsApp — Baileys
 *
 * Conecta ao WhatsApp Business profissional, escuta mensagens,
 * encaminha para o receptor Python (Hermes Secretary) e envia respostas.
 *
 * Uso:
 *   npm install
 *   node gateway.js
 *   # Escaneie o QR code com o celular do WhatsApp Business profissional
 */
const {
  default: makeWASocket,
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
} = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const http = require('http');
const CONFIG = require('./config');

// ── Estado global ──────────────────────────────────────────────────
let sock = null;
let reconnectAttempts = 0;
const MAX_RECONNECT = 10;

// ── Logging ──────────────────────────────────────────────────────
function log(level, ...args) {
  const levels = { debug: 0, info: 1, warn: 2, error: 3 };
  if (levels[level] >= levels[CONFIG.LOG_LEVEL]) {
    const ts = new Date().toISOString();
    console.log(`[${ts}] [${level.toUpperCase()}]`, ...args);
  }
}

// ── Whitelist ────────────────────────────────────────────────────
function isWhatsappNumberAllowed(jid) {
  if (!CONFIG.WHITELIST || CONFIG.WHITELIST.length === 0) {
    // Se whitelist vazia, BLOQUEIA TUDO (modo seguro)
    return false;
  }
  const number = jid.split('@')[0];
  return CONFIG.WHITELIST.some((w) => {
    const clean = w.replace(/\D/g, '');
    return number.endsWith(clean) || clean.endsWith(number);
  });
}

// ── Envia mensagem para o receptor Python ─────────────────────────
async function forwardToHermes(messageData) {
  try {
    const res = await axios.post(
      CONFIG.HERMES_RECEIVER_URL,
      messageData,
      { timeout: 30000 }
    );
    return res.data;
  } catch (err) {
    log('error', 'Falha ao enviar para receptor:', err.message);
    return null;
  }
}

// ── Envia resposta de volta ao WhatsApp ──────────────────────────
async function sendWhatsappResponse(jid, text) {
  if (!sock) {
    log('error', 'Socket não conectado. Não foi possível enviar resposta.');
    return;
  }
  // Delay humanizado
  await new Promise((r) => setTimeout(r, CONFIG.MIN_RESPONSE_DELAY));
  try {
    await sock.sendMessage(jid, { text });
    log('info', `Resposta enviada para ${jid}`);
  } catch (err) {
    log('error', 'Erro ao enviar mensagem:', err.message);
  }
}

// ── Processa mensagem recebida ───────────────────────────────────
async function handleIncomingMessage(msg) {
  if (!msg.message) return;

  const jid = msg.key.remoteJid;
  if (!jid || !jid.endsWith('@s.whatsapp.net')) return;

  // Só responde números na whitelist
  if (!isWhatsappNumberAllowed(jid)) {
    log('warn', `Mensagem de número não autorizado ignorada: ${jid}`);
    return;
  }

  // Extrai texto ou tipo
  const text =
    msg.message.conversation ||
    msg.message.extendedTextMessage?.text ||
    '';

  const type = Object.keys(msg.message)[0];
  log('info', `Mensagem de ${jid}: "${text.substring(0, 100)}" (tipo: ${type})`);

  const payload = {
    from: jid,
    text: text,
    type: type,
    timestamp: msg.messageTimestamp,
    message_id: msg.key.id,
  };

  // Se for áudio, adiciona flag
  if (type === 'audioMessage') {
    payload.is_audio = true;
    // Baileys pode fornecer URL do áudio, mas download é complexo.
    // Por ora, marca como áudio para o receptor saber que precisa STT.
  }

  // Envia para o receptor Python
  const response = await forwardToHermes(payload);

  // Se o receptor respondeu com texto, envia de volta
  if (response && response.response) {
    await sendWhatsappResponse(jid, response.response);
  }
}

// ── Inicializa conexão ────────────────────────────────────────────
async function startGateway() {
  log('info', 'Iniciando Hermes WhatsApp Gateway...');

  const { state, saveCreds } = await useMultiFileAuthState(CONFIG.AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    printQRInTerminal: false, // Usamos qrcode-terminal manualmente
    auth: state,
    // Mantém conexão mesmo se o celular desconectar da internet
    syncFullHistory: false,
    markOnlineOnConnect: false,
  });

  // QR Code
  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      log('info', 'QR Code recebido. Escaneie com o WhatsApp Business:');
      qrcode.generate(qr, { small: true });
    }

    if (connection === 'close') {
      const shouldReconnect =
        lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
      log('warn', 'Conexão fechada. Motivo:', lastDisconnect?.error?.output?.statusCode);

      if (shouldReconnect && reconnectAttempts < MAX_RECONNECT) {
        reconnectAttempts++;
        log('info', `Tentando reconectar... (${reconnectAttempts}/${MAX_RECONNECT})`);
        setTimeout(startGateway, 5000);
      } else {
        log('error', 'Reconexão abortada. Verifique o QR code ou reinicie manualmente.');
      }
    } else if (connection === 'open') {
      reconnectAttempts = 0;
      log('info', '✅ Conectado ao WhatsApp!');
    }
  });

  // Credenciais atualizadas
  sock.ev.on('creds.update', saveCreds);

  // Nova mensagem
  sock.ev.on('messages.upsert', async (m) => {
    if (m.type === 'notify') {
      for (const msg of m.messages) {
        await handleIncomingMessage(msg);
      }
    }
  });
}

// ── HTTP server para receber respostas do Python ──────────────────
function startHttpServer() {
  const server = http.createServer(async (req, res) => {
    if (req.method === 'POST' && req.url === '/send') {
      let body = '';
      req.on('data', (chunk) => (body += chunk));
      req.on('end', async () => {
        try {
          const data = JSON.parse(body);
          if (data.jid && data.text) {
            await sendWhatsappResponse(data.jid, data.text);
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ sent: true }));
          } else {
            res.writeHead(400);
            res.end(JSON.stringify({ error: 'jid e text são obrigatórios' }));
          }
        } catch (err) {
          res.writeHead(400);
          res.end(JSON.stringify({ error: err.message }));
        }
      });
    } else {
      res.writeHead(404);
      res.end(JSON.stringify({ error: 'Not found' }));
    }
  });

  server.listen(CONFIG.GATEWAY_PORT, () => {
    log('info', `HTTP server do gateway ouvindo em http://localhost:${CONFIG.GATEWAY_PORT}/send`);
  });
}

// ── Entry point ────────────────────────────────────────────────────
(async () => {
  startHttpServer();
  await startGateway();
})();
