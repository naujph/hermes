"""WhatsAppParser — Parser de export TXT do WhatsApp pessoal.

Formato esperado (export nativo do WhatsApp Android/iOS):
[dd/mm/yy, hh:mm] Nome do Contato: mensagem
[dd/mm/yy, hh:mm] +55 11 99999-9999: mensagem

Filtro: processa apenas contatos cujo nome começa com 'Cliente'.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any


class WhatsAppParser:
    """Lê arquivo .txt exportado do WhatsApp e retorna mensagens estruturadas."""

    WHATSAPP_RE = re.compile(
        r"^\[(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}),?\s*(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+?)\s*:\s*(.+)$"
    )

    def __init__(self, my_name: str = "Juan", client_prefix: str = "Cliente"):
        self.my_name = my_name
        self.client_prefix = client_prefix

    def parse_file(self, filepath: str | Path) -> list[dict]:
        text = Path(filepath).read_text(encoding="utf-8", errors="ignore")
        return self.parse_text(text)

    def parse_text(self, text: str) -> list[dict]:
        messages = []
        current_author = ""
        current_text = ""
        current_ts = ""

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            m = self.WHATSAPP_RE.match(line)
            if m:
                # Salva mensagem anterior
                if current_author and current_text:
                    messages.append(self._build_msg(current_author, current_text, current_ts))
                date_str, time_str, author, msg = m.groups()
                current_author = author.strip()
                current_text = msg.strip()
                current_ts = self._normalize_ts(date_str, time_str)
            else:
                # Continuação da mensagem anterior
                if current_author:
                    current_text += "\n" + line

        # última
        if current_author and current_text:
            messages.append(self._build_msg(current_author, current_text, current_ts))

        # Filtra apenas contatos que começam com o prefixo (ou meus próprios)
        filtered = [
            m for m in messages
            if m["author"].startswith(self.client_prefix) or m["is_me"]
        ]
        return filtered

    def _build_msg(self, author: str, text: str, ts: str) -> dict:
        is_me = author == self.my_name or "você" in author.lower()
        return {
            "author": author,
            "text": text,
            "timestamp": ts,
            "is_me": is_me,
        }

    def _normalize_ts(self, date_str: str, time_str: str) -> str:
        """Tenta converter para ISO8601; se falhar, retorna original."""
        for fmt in ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M", "%m/%d/%y %H:%M", "%d-%m-%y %H:%M"):
            try:
                dt = datetime.strptime(f"{date_str.strip()} {time_str.strip()}", fmt)
                return dt.isoformat()
            except ValueError:
                continue
        return f"{date_str} {time_str}"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        p = WhatsAppParser()
        msgs = p.parse_file(sys.argv[1])
        print(f"Mensagens filtradas: {len(msgs)}")
        for m in msgs[:5]:
            print(f"[{m['timestamp']}] {m['author']} (eu={m['is_me']}): {m['text'][:80]}...")
