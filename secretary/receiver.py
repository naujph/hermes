"""Hermes Secretary Receiver — Servidor HTTP leve para receber mensagens do Gateway WhatsApp.

Recebe POST /receive com JSON do gateway, processa via HermesCore,
e retorna resposta para o gateway enviar de volta ao WhatsApp.

Também aceita POST /send para enviar mensagens proativas.

Uso:
    python receiver.py
    # Escuta em http://localhost:8765
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes.secretary.core import HermesCore

# Instância global do cérebro
core = HermesCore()


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silencia logs padrão do HTTP server
        pass

    def _send_json(self, status: int, data: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send_json(400, {"error": "JSON inválido"})
            return

        if self.path == "/receive":
            # Mensagem do WhatsApp Gateway
            text = payload.get("text", "")
            from_jid = payload.get("from", "")
            print(f"[WhatsApp] {from_jid}: {text[:80]}")

            result = core.process_message(text)
            response_text = result.get("response", "Desculpe, não entendi.")

            self._send_json(200, {"response": response_text, "tool_used": result.get("tool_used")})
            print(f"[Hermes] Resposta enviada ({len(response_text)} chars)")

        elif self.path == "/send":
            # Mensagem proativa (ex: digest automático da manhã)
            text = payload.get("text", "")
            result = core.process_message(text)
            self._send_json(200, {"response": result.get("response", "")})

        else:
            self._send_json(404, {"error": "Endpoint não encontrado. Use /receive ou /send"})

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "history_size": len(core.history)})
        else:
            self._send_json(404, {"error": "Not found"})


def run_server(port: int = 8765) -> None:
    server = HTTPServer(("localhost", port), RequestHandler)
    print(f"🟢 Hermes Receiver ouvindo em http://localhost:{port}")
    print(f"   Endpoints: POST /receive | POST /send | GET /health")
    print("   Pressione Ctrl+C para parar.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🔴 Servidor encerrado.")
        server.shutdown()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    run_server(port)
