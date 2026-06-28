"""Tool: query_db — Consulta segura no SQLite."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection


DENYLIST = {"delete", "drop", "truncate", "alter table", "create table", "insert into", "update "}


def run_query(query: str) -> dict:
    """Executa query SQL no banco. Só permite SELECTs automaticamente."""
    q_lower = query.lower().strip()

    # Bloqueia comandos destrutivos automaticamente
    for forbidden in DENYLIST:
        if forbidden in q_lower and not q_lower.lstrip().startswith("select"):
            return {
                "success": False,
                "error": f"Comando '{forbidden}' não permitido. Use SELECT para consultas.",
                "rows": [],
            }

    # Se não começa com SELECT, exige explicação (mas ainda bloqueia write)
    if not q_lower.startswith("select") and not q_lower.startswith("with"):
        return {
            "success": False,
            "error": "Apenas consultas SELECT são permitidas automaticamente. Para INSERT/UPDATE/DELETE, use as ferramentas específicas.",
            "rows": [],
        }

    try:
        with get_connection() as conn:
            rows = conn.execute(query).fetchall()
            result = [dict(r) for r in rows]
        return {"success": True, "rows": result, "count": len(result)}
    except Exception as exc:
        return {"success": False, "error": str(exc), "rows": []}
