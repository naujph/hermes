#!/usr/bin/env python3
"""Skill: suggest_follow_up

Analisa leads que precisam de follow-up e sugere próximos passos.
Recebe JSON via stdin, retorna JSON via stdout.
"""
import json
import sys
from datetime import datetime, UTC, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "errors": ["JSON inválido"]}))
        sys.exit(1)

    lead_id = payload.get('lead_id')
    days = payload.get('days_since_last_contact', 3)
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    with get_connection() as conn:
        if lead_id:
            rows = conn.execute(
                '''
                SELECT l.id, l.company_name, l.phone, l.whatsapp_number, l.email,
                       l.conversation_status, l.temperature, l.score_total, l.last_contact_at,
                       c.razao_social, c.cnpj
                FROM leads l
                LEFT JOIN companies c ON c.id = l.company_id
                WHERE l.id = ? AND (l.last_contact_at IS NULL OR l.last_contact_at < ?)
                ''',
                (lead_id, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                '''
                SELECT l.id, l.company_name, l.phone, l.whatsapp_number, l.email,
                       l.conversation_status, l.temperature, l.score_total, l.last_contact_at,
                       c.razao_social, c.cnpj
                FROM leads l
                LEFT JOIN companies c ON c.id = l.company_id
                WHERE l.conversation_status IN ('em_contato', 'respondeu', 'aprovado_contato')
                  AND (l.last_contact_at IS NULL OR l.last_contact_at < ?)
                ORDER BY l.score_total DESC, l.last_contact_at ASC
                LIMIT 20
                ''',
                (cutoff,),
            ).fetchall()

    suggestions = []
    for row in rows:
        lead = dict(row)
        channel = 'whatsapp' if lead.get('whatsapp_number') else 'email' if lead.get('email') else 'telefone'
        last = lead.get('last_contact_at') or 'Nunca'
        suggestions.append({
            'lead_id': lead['id'],
            'company_name': lead.get('company_name') or lead.get('razao_social'),
            'channel': channel,
            'last_contact': last,
            'score': lead.get('score_total', 0),
            'temperature': lead.get('temperature'),
            'suggested_text': f"Olá, tudo bem? Só passando para retomar nossa conversa sobre assessoria. Quando ficar no timing, me avisa.",
            'reason': f"Sem contato há {days}+ dias, status: {lead.get('conversation_status')}",
        })

    print(json.dumps({
        "success": True,
        "suggestions": suggestions,
        "count": len(suggestions),
        "logs": [f"Analisados {len(suggestions)} leads para follow-up"],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
