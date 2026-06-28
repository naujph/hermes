"""Tool: cancel_meeting — Cancela uma reunião existente."""
from __future__ import annotations

import sys
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection


def cancel_meeting(meeting_id: int | None = None, title_fragment: str | None = None, reason: str = "Cancelado via Hermes Secretary") -> dict:
    """Cancela uma reunião por ID ou buscando por fragmento do título/horário."""
    if not meeting_id and not title_fragment:
        return {"success": False, "error": "Informe meeting_id ou title_fragment."}

    with get_connection() as conn:
        if meeting_id:
            row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        else:
            # Busca por fragmento do título
            row = conn.execute(
                "SELECT * FROM meetings WHERE title LIKE ? AND meeting_status != 'cancelada' ORDER BY scheduled_start DESC LIMIT 1",
                (f"%{title_fragment}%",),
            ).fetchone()

        if not row:
            return {"success": False, "error": "Reunião não encontrada."}

        meeting = dict(row)
        now = datetime.now(UTC).isoformat()

        conn.execute(
            "UPDATE meetings SET meeting_status = 'cancelada', notes = COALESCE(notes, '') || ? || ?, updated_at = ? WHERE id = ?",
            (f"\n\n[CANCELADO] {reason}", f"\nCancelado em: {now}", now, meeting["id"]),
        )

    return {
        "success": True,
        "message": f"Reunião '{meeting['title']}' cancelada. Motivo: {reason}",
        "meeting_id": meeting["id"],
    }
