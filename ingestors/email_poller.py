"""EmailPoller — Polling IMAP simples para email comercial.

Lê caixa de entrada, extrai emails novos (desde último check), retorna estrutura
compatível com ConversationIngestor.
"""
from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import json
import os
import re
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class EmailPoller:
    """Conecta via IMAP e lê emails novos."""

    def __init__(
        self,
        host: str | None = None,
        user: str | None = None,
        password: str | None = None,
        folder: str = "INBOX",
        state_file: str | None = None,
    ):
        self.host = host or os.getenv("EMAIL_IMAP_HOST", "imap.gmail.com")
        self.user = user or os.getenv("EMAIL_USER", "")
        self.password = password or os.getenv("EMAIL_PASSWORD", "")
        self.folder = folder
        self.state_file = state_file or str(
            Path(__file__).resolve().parent.parent / "memory" / "email_poller_state.json"
        )
        self._last_uid = self._load_state()

    # ── Público ────────────────────────────────────────────────────────

    def poll(self, max_emails: int = 20) -> list[dict]:
        """Retorna lista de emails como 'conversas' para o ingestor."""
        if not self.user or not self.password:
            print("[EmailPoller] EMAIL_USER ou EMAIL_PASSWORD não configurados.")
            return []

        mails = []
        try:
            ctx = ssl.create_default_context()
            with imaplib.IMAP4_SSL(self.host, ssl_context=ctx) as conn:
                conn.login(self.user, self.password)
                conn.select(self.folder)

                # Busca todos (vamos filtrar por UID depois)
                typ, data = conn.search(None, "ALL")
                if typ != "OK":
                    return []
                uids = data[0].split()
                new_uids = [u for u in uids if int(u) > self._last_uid]
                if not new_uids:
                    return []

                for uid in new_uids[-max_emails:]:
                    typ, msg_data = conn.fetch(uid, "(RFC822)")
                    if typ != "OK" or not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)
                    parsed = self._parse_email(msg)
                    if parsed:
                        mails.append(parsed)
                    self._last_uid = max(self._last_uid, int(uid))

                self._save_state()
        except Exception as exc:
            print(f"[EmailPoller] Erro: {exc}")
        return mails

    # ── Internals ────────────────────────────────────────────────────

    def _parse_email(self, msg: email.message.Message) -> dict | None:
        subject = self._decode_header(msg.get("Subject", ""))
        from_ = self._decode_header(msg.get("From", ""))
        date_str = msg.get("Date", "")
        body = self._extract_body(msg)

        # Ignora newsletters/spam por palavras-chave
        spammy = ["unsubscribe", "promoção", "promocao", "oferta", "marketing", "noreply"]
        lower = f"{subject} {body}".lower()
        if any(s in lower for s in spammy):
            return None

        return {
            "author": from_,
            "text": f"Assunto: {subject}\n\n{body}",
            "timestamp": self._normalize_date(date_str),
            "is_me": False,
            "source": "email",
            "metadata": {
                "subject": subject,
                "from": from_,
                "date": date_str,
            },
        }

    def _extract_body(self, msg: email.message.Message) -> str:
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    try:
                        payload = part.get_payload(decode=True)
                        if payload:
                            body += payload.decode("utf-8", errors="ignore")
                    except Exception:
                        pass
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="ignore")
            except Exception:
                pass
        # Limpa citações
        lines = body.splitlines()
        clean = []
        for line in lines:
            if line.strip().startswith(>") or line.strip().startswith("On ") and "wrote:" in line:
                break
            clean.append(line)
        return "\n".join(clean).strip()

    def _decode_header(self, value: str) -> str:
        parts = email.header.decode_header(value)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="ignore"))
            else:
                decoded.append(str(part))
        return " ".join(decoded)

    def _normalize_date(self, date_str: str) -> str:
        try:
            dt = email.utils.parsedate_to_datetime(date_str)
            return dt.isoformat()
        except Exception:
            return datetime.now(timezone.utc).isoformat()

    def _load_state(self) -> int:
        try:
            if Path(self.state_file).exists():
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f).get("last_uid", 0)
        except Exception:
            pass
        return 0

    def _save_state(self) -> None:
        try:
            Path(self.state_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({"last_uid": self._last_uid, "checked_at": datetime.now(timezone.utc).isoformat()}, f)
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    poller = EmailPoller()
    mails = poller.poll(max_emails=5)
    print(f"Novos emails: {len(mails)}")
    for m in mails:
        print(f"- {m['author']}: {m['metadata']['subject'][:60]}")
