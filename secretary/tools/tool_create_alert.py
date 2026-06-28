"""Tool: create_alert — Cria alerta no painel Streamlit."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection
from datetime import datetime, UTC


def create_alert(
    alert_type: str,
    title: str,
    description: str,
    lead_id: int | None = None,
    company_id: int | None = None,
    suggested_action: str | None = None,
) -> dict:
    """Cria um alerta na tabela hermes_alerts para o painel consumir."""
    now = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            '''
            INSERT INTO hermes_alerts
            (alert_type, lead_id, company_id, title, description, suggested_action, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (alert_type, lead_id, company_id, title, description, suggested_action, 'novo', now),
        )
        alert_id = cursor.lastrowid

    return {
        "success": True,
        "message": f"Alerta #{alert_id} criado: {title}",
        "alert_id": alert_id,
    }
