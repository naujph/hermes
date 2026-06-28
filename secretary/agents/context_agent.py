"""ContextAgent — Coleta contexto completo antes do planejamento."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes.secretary.context.personal_memory import PersonalMemory
from hermes.memory.retriever import Retriever


class ContextAgent:
    """Monta o contexto que o Planner precisa para decidir."""

    def __init__(
        self,
        memory: PersonalMemory | None = None,
        retriever: Retriever | None = None,
    ):
        self.memory = memory or PersonalMemory()
        self.retriever = retriever or Retriever()

    def gather(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Retorna contexto estruturado."""
        result: dict[str, Any] = {
            "profile": self.memory.get_profile_text(),
            "preferences": self.memory.get_preferences_text(),
            "projects": self.memory.get_projects_text(),
            "convex": self.retriever.build_context_prompt(user_message, top_k=4),
            "snapshot": self._build_snapshot_text(),
            "history": self._format_history(history or []),
        }
        return result

    def to_prompt(self, context: dict[str, Any]) -> str:
        """Converte contexto em bloco de texto para prompt."""
        parts = [
            "### CONTEXTO DO JUAN (use para tomar decisões melhores)",
            "",
            context.get("profile", ""),
            context.get("preferences", ""),
            context.get("projects", ""),
            context.get("snapshot", ""),
            context.get("convex", ""),
            context.get("history", ""),
        ]
        return "\n\n".join(p for p in parts if p and p.strip())

    def _format_history(self, history: list[dict[str, Any]], max_turns: int = 6) -> str:
        if not history:
            return ""
        lines = ["### ÚLTIMAS MENSAGENS"]
        for entry in history[-max_turns:]:
            user = entry.get("user", "")
            assistant = entry.get("assistant_raw", "")
            tool = entry.get("tool", "")
            lines.append(f"Juan: {user}")
            if tool and tool != "direct_response":
                lines.append(f"[Hermes usou {tool}]")
            lines.append(f"Hermes: {assistant[:300]}")
            lines.append("")
        return "\n".join(lines)

    def _build_snapshot_text(self) -> str:
        try:
            from app.database import get_connection
            from datetime import datetime, UTC

            with get_connection() as conn:
                total_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
                hot_leads = conn.execute("SELECT COUNT(*) FROM leads WHERE hot_lead = 1").fetchone()[0]
                open_opps = conn.execute("SELECT COUNT(*) FROM opportunities WHERE status = 'aberta'").fetchone()[0]
                today_meetings = conn.execute(
                    "SELECT COUNT(*) FROM meetings WHERE DATE(scheduled_start) = DATE('now')"
                ).fetchone()[0]
                pending_alerts = conn.execute(
                    "SELECT COUNT(*) FROM hermes_alerts WHERE status = 'novo'"
                ).fetchone()[0]

            return (
                f"### SNAPSHOT COMERCIAL ({datetime.now(UTC).strftime('%H:%M')}):\n"
                f"- Leads totais: {total_leads} | Hot: {hot_leads}\n"
                f"- Oportunidades abertas: {open_opps}\n"
                f"- Reuniões hoje: {today_meetings}\n"
                f"- Alertas pendentes: {pending_alerts}"
            )
        except Exception:
            return ""
