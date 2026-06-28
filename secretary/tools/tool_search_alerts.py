"""Tool: search_alerts — Busca alertas, conhecimento e notas no hermes_alerts."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection


ALIAS_MAP = {
    "conhecimento": ["conhecimento", "nota", "escritorio", "escritório"],
    "nota": ["nota", "conhecimento", "escritorio", "escritório"],
    "oportunidade": ["oportunidade"],
    "marketing": ["marketing", "brief"],
    "insight": ["insight"],
    "info": ["info"],
    "warning": ["warning"],
    "action_required": ["action_required"],
}


def _resolve_types(type_hint: str | None) -> list[str] | None:
    if not type_hint:
        return None
    t = type_hint.lower().strip()
    if t in ALIAS_MAP:
        return ALIAS_MAP[t]
    return [t]


def search_alerts(
    query: str = "",
    alert_type: str | None = None,
    status: str | None = None,
    days: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Busca alertas/conhecimento/notas no hermes_alerts.

    Args:
        query: palavras-chave para buscar em title/description.
        alert_type: filtra por tipo (conhecimento, nota, oportunidade, marketing, insight, info, warning, action_required).
        status: filtra por status (novo, visto, resolvido, etc).
        days: busca apenas os últimos N dias.
        limit: máximo de resultados.
    """
    try:
        types = _resolve_types(alert_type)
        conditions = []
        params: list[Any] = []

        if types:
            placeholders = ",".join("?" for _ in types)
            conditions.append(f"alert_type IN ({placeholders})")
            params.extend(types)

        if status:
            conditions.append("status = ?")
            params.append(status)

        if days and days > 0:
            since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
            conditions.append("created_at >= ?")
            params.append(since)

        if query and len(query.strip()) >= 2:
            q = f"%{query.strip()}%"
            conditions.append("(title LIKE ? OR description LIKE ?)")
            params.extend([q, q])

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        sql = f"""
            SELECT id, alert_type, status, title, description, suggested_action, lead_id, company_id, created_at
            FROM hermes_alerts
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ?
        """
        params.append(limit)

        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            results = [dict(r) for r in rows]

        if not results:
            return {
                "success": True,
                "count": 0,
                "rows": [],
                "message": "Nenhum alerta/nota encontrado com esses critérios.",
            }

        return {
            "success": True,
            "count": len(results),
            "rows": results,
            "message": f"Encontrados {len(results)} registros.",
        }

    except Exception as exc:
        return {"success": False, "error": str(exc), "rows": [], "count": 0}
