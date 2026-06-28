#!/usr/bin/env python3
"""Skill: create_alert

Cria um alerta na tabela hermes_alerts para o painel mostrar.
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


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "errors": ["JSON inválido"]}))
        sys.exit(1)

    alert_type = payload.get('alert_type')
    title = payload.get('title')
    description = payload.get('description')

    if not alert_type or not title or not description:
        print(json.dumps({"success": False, "errors": ["alert_type, title e description são obrigatórios"]}))
        sys.exit(1)

    now = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            '''
            INSERT INTO hermes_alerts (alert_type, lead_id, company_id, title, description, suggested_action, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                alert_type,
                payload.get('lead_id'),
                payload.get('company_id'),
                title,
                description,
                payload.get('suggested_action'),
                'novo',
                now,
            ),
        )
        alert_id = cursor.lastrowid

    print(json.dumps({
        "success": True,
        "alert_id": alert_id,
        "logs": [f"Alerta {alert_id} criado: {title}"],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
