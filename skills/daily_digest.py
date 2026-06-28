#!/usr/bin/env python3
"""Skill: daily_digest

Gera resumo diário da operação.
Recebe JSON via stdin (vazio), retorna JSON via stdout.
"""
import json
import sys
from datetime import datetime, UTC, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Garante UTF-8 no stdout no Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from app.database import get_connection


def main():
    today = datetime.now(UTC).date().isoformat()
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()

    with get_connection() as conn:
        # Leads novos hoje
        new_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE DATE(created_at) = ?", (today,)
        ).fetchone()[0]

        # Leads enriquecidos hoje
        enriched = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE conversation_status = 'enriquecido' AND DATE(updated_at) = ?", (today,)
        ).fetchone()[0]

        # Reuniões hoje
        meetings = conn.execute(
            "SELECT COUNT(*) FROM meetings WHERE DATE(scheduled_start) = ?", (today,)
        ).fetchone()[0]

        # Follow-ups pendentes
        followups = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE conversation_status IN ('em_contato', 'respondeu') AND (last_contact_at IS NULL OR last_contact_at < ?)",
            ((datetime.now(UTC) - timedelta(days=3)).isoformat(),),
        ).fetchone()[0]

        # Hot leads
        hot = conn.execute("SELECT COUNT(*) FROM leads WHERE hot_lead = 1").fetchone()[0]

        # Oportunidades abertas
        opps = conn.execute("SELECT COUNT(*) FROM opportunities WHERE status = 'aberta'").fetchone()[0]

    lines = []
    lines.append(f"Resumo do dia {today}")
    lines.append("")
    lines.append(f"Leads novos: {new_leads}")
    lines.append(f"Leads enriquecidos: {enriched}")
    lines.append(f"Hot leads na base: {hot}")
    lines.append(f"Reunioes hoje: {meetings}")
    lines.append(f"Follow-ups pendentes: {followups}")
    lines.append(f"Oportunidades abertas: {opps}")
    lines.append("")

    recommendations = []
    if followups > 0:
        lines.append("Acao recomendada: revisar follow-ups pendentes.")
        recommendations.append({"what": "Revisar follow-ups pendentes", "priority": "high", "reason": f"{followups} leads aguardando retorno"})
    if meetings > 0:
        lines.append("Confira seus briefings antes das reunioes.")
        recommendations.append({"what": "Preparar briefings das reuniões", "priority": "medium", "reason": f"{meetings} reuniões hoje"})
    if hot > 0:
        recommendations.append({"what": "Priorizar leads hot", "priority": "high", "reason": f"{hot} lead(s) hot na base"})
    if opps > 0:
        recommendations.append({"what": "Revisar oportunidades abertas", "priority": "medium", "reason": f"{opps} oportunidade(s) aberta(s)"})

    digest = "\n".join(lines)

    # Detalha entidades para ação vinculada
    with get_connection() as conn:
        lead_ids_today = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM leads WHERE DATE(created_at) = ? ORDER BY score_total DESC LIMIT 20", (today,)
            ).fetchall()
        ]
        meeting_ids_today = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM meetings WHERE DATE(scheduled_start) = ? ORDER BY scheduled_start ASC", (today,)
            ).fetchall()
        ]
        hot_lead_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM leads WHERE hot_lead = 1 ORDER BY score_total DESC LIMIT 10"
            ).fetchall()
        ]

    print(json.dumps({
        "success": True,
        "digest": digest,
        "metrics": {
            "new_leads": new_leads,
            "enriched": enriched,
            "hot_leads": hot,
            "meetings_today": meetings,
            "followups_pending": followups,
            "open_opportunities": opps,
        },
        "entities": {
            "new_lead_ids": lead_ids_today,
            "meeting_ids_today": meeting_ids_today,
            "hot_lead_ids": hot_lead_ids,
        },
        "recommendations": recommendations,
        "logs": ["Digest gerado com sucesso"],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
