#!/usr/bin/env python3
"""Skill: market_monitor

Monitora oportunidades de mercado baseado nos leads e dados do banco.
Gera alertas proativos: leads quentes sem contato, oportunidades estagnadas, follow-ups pendentes.
Recebe JSON via stdin, retorna JSON via stdout.

Uso:
    echo '{}' | python hermes/skills/market_monitor.py
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


def monitor_opportunities() -> dict:
    now = datetime.now(UTC)
    alerts = []

    with get_connection() as conn:
        # 1. Leads quentes sem contato há > 3 dias
        three_days_ago = (now - timedelta(days=3)).isoformat()
        hot_no_contact = conn.execute(
            "SELECT id, company_name, temperature, last_contact_at FROM leads WHERE hot_lead = 1 AND (last_contact_at IS NULL OR last_contact_at < ?)",
            (three_days_ago,),
        ).fetchall()
        for row in hot_no_contact:
            alerts.append({
                "type": "urgente",
                "title": f"Lead HOT sem contato: {row['company_name']}",
                "description": f"Temperatura: {row['temperature']}. Último contato: {row['last_contact_at'] or 'Nunca'}",
                "lead_id": row["id"],
                "suggested_action": "Ligar ou mandar WhatsApp HOJE.",
            })

        # 2. Oportunidades em negociação há > 30 dias sem movimento
        thirty_days_ago = (now - timedelta(days=30)).isoformat()
        stale_opps = conn.execute(
            "SELECT id, title, stage, company_id FROM opportunities WHERE stage = 'negociacao' AND updated_at < ?",
            (thirty_days_ago,),
        ).fetchall()
        for row in stale_opps:
            alerts.append({
                "type": "aviso",
                "title": f"Negociação estagnada: {row['title']}",
                "description": f"Em negociação há mais de 30 dias sem movimento.",
                "opp_id": row["id"],
                "suggested_action": "Reavaliar proposta ou qualificar objeções.",
            })

        # 3. Reuniões amanhã sem briefing gerado
        tomorrow = (now + timedelta(days=1)).date().isoformat()
        tomorrow_meetings = conn.execute(
            """SELECT m.id, m.title, m.scheduled_start, l.id as lead_id, l.company_name
               FROM meetings m JOIN leads l ON l.id = m.lead_id
               WHERE DATE(m.scheduled_start) = ? AND m.meeting_status = 'agendada'""",
            (tomorrow,),
        ).fetchall()
        for row in tomorrow_meetings:
            alerts.append({
                "type": "info",
                "title": f"Reunião amanhã: {row['title']}",
                "description": f"Com {row['company_name']} às {row['scheduled_start']}",
                "meeting_id": row["id"],
                "lead_id": row["lead_id"],
                "suggested_action": "Gerar briefing antes da reunião.",
            })

        # 4. Leads em 'revisao_pendente' há > 7 dias
        seven_days_ago = (now - timedelta(days=7)).isoformat()
        pending_review = conn.execute(
            "SELECT id, company_name, created_at FROM leads WHERE conversation_status = 'revisao_pendente' AND created_at < ?",
            (seven_days_ago,),
        ).fetchall()
        for row in pending_review:
            alerts.append({
                "type": "aviso",
                "title": f"Revisão pendente: {row['company_name']}",
                "description": "Lead em revisão há mais de 7 dias.",
                "lead_id": row["id"],
                "suggested_action": "Revisar e aprovar/rejeitar lead.",
            })

    if not alerts:
        return {"success": True, "alerts": [], "message": "🟢 Nenhum alerta proativo no momento. Operação limpa!"}

    lines = [f"🚨 {len(alerts)} alerta(s) encontrado(s):"]
    for a in alerts:
        emoji = {"urgente": "🔴", "aviso": "🟡", "info": "🔵"}.get(a["type"], "⚪")
        lines.append(f"\n{emoji} **{a['title']}**")
        lines.append(f"   {a['description']}")
        if a.get("suggested_action"):
            lines.append(f"   💡 Ação: {a['suggested_action']}")

    return {"success": True, "alerts": alerts, "message": "\n".join(lines)}


def main():
    try:
        if sys.stdin.isatty():
            payload = {}
        else:
            raw = sys.stdin.buffer.read().decode("utf-8")
            payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}

    result = monitor_opportunities()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
