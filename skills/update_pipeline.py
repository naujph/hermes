#!/usr/bin/env python3
"""Skill: update_pipeline

Atualiza estágio de oportunidade com validação de transições.
Recebe JSON via stdin, retorna JSON via stdout.
"""
import json
import sys
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection

VALID_TRANSITIONS = {
    'prospeccao': {'contato_inicial', 'perdido'},
    'contato_inicial': {'reuniao_agendada', 'prospeccao', 'perdido'},
    'reuniao_agendada': {'proposta_enviada', 'contato_inicial', 'perdido'},
    'proposta_enviada': {'negociacao', 'reuniao_agendada', 'perdido'},
    'negociacao': {'ganho', 'proposta_enviada', 'perdido'},
    'ganho': set(),
    'perdido': set(),
}


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "errors": ["JSON inválido"]}))
        sys.exit(1)

    opp_id = payload.get('opportunity_id')
    new_stage = payload.get('new_stage')
    notes = payload.get('notes', '')

    if not opp_id or not new_stage:
        print(json.dumps({"success": False, "errors": ["opportunity_id e new_stage são obrigatórios"]}))
        sys.exit(1)

    with get_connection() as conn:
        row = conn.execute('SELECT * FROM opportunities WHERE id = ?', (opp_id,)).fetchone()
        if not row:
            print(json.dumps({"success": False, "errors": [f"Oportunidade {opp_id} não encontrada"]}))
            sys.exit(1)

        current = dict(row)
        prev_stage = current['stage']

        if new_stage not in VALID_TRANSITIONS.get(prev_stage, set()):
            print(json.dumps({
                "success": False,
                "errors": [f"Transição inválida: {prev_stage} -> {new_stage}"],
                "valid_next": list(VALID_TRANSITIONS.get(prev_stage, set())),
            }))
            sys.exit(1)

        now = datetime.now(UTC).isoformat()
        updates = {'stage': new_stage, 'updated_at': now}
        if new_stage == 'ganho':
            updates['status'] = 'fechada'
            updates['actual_close_date'] = now
        elif new_stage == 'perdido':
            updates['status'] = 'fechada'
            updates['lost_reason'] = notes or 'Não informado'

        assignments = ', '.join([f"{k} = ?" for k in updates])
        conn.execute(f"UPDATE opportunities SET {assignments} WHERE id = ?", (*updates.values(), opp_id))

    print(json.dumps({
        "success": True,
        "opportunity_id": opp_id,
        "previous_stage": prev_stage,
        "new_stage": new_stage,
        "logs": [f"Oportunidade {opp_id}: {prev_stage} -> {new_stage}"],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
