"""hermes.ingestors — Ingestão de conversas para memória convexa."""
from __future__ import annotations

from hermes.ingestors.conversation_ingestor import ConversationIngestor
from hermes.ingestors.whatsapp_parser import WhatsAppParser
from hermes.ingestors.email_poller import EmailPoller

__all__ = ["ConversationIngestor", "WhatsAppParser", "EmailPoller"]
