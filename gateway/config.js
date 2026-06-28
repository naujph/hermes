/**
 * Configuração do Gateway WhatsApp Hermes
 */
const path = require('path');

const CONFIG = {
  // Whitelist de números que podem interagir com o Hermes
  // Formato: +55DDDNNNNNNNN (somente Juan por enquanto)
  WHITELIST: [
    // ADICIONE SEU NÚMERO PESSOAL AQUI (ex: '+5511999998888')
    // Exemplo: '+5511999998888'
  ],

  // Endpoint do receptor Python (Hermes Secretary)
  HERMES_RECEIVER_URL: 'http://localhost:8765/receive',

  // Porta do próprio gateway (para receber respostas do Python)
  GATEWAY_PORT: 8766,

  // Diretório de autenticação da sessão WhatsApp
  AUTH_DIR: path.join(__dirname, 'auth'),

  // Delay mínimo entre respostas (ms) para evitar comportamento robótico
  MIN_RESPONSE_DELAY: 800,

  // Log level
  LOG_LEVEL: 'info', // 'debug' | 'info' | 'warn' | 'error'
};

module.exports = CONFIG;
