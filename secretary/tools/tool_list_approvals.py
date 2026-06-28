"""Tool: list_approvals — Lista aprovações pendentes do Hermes."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_env
from app.repositories.approval_repository import ApprovalRepository

load_env()


def list_approvals(status: str = "pending", limit: int = 20) -> dict:
    """Lista pending_approvals por status.

    Args:
        status: 'pending', 'approved', 'rejected', 'expired', 'auto_executed' ou None para todos.
        limit: maximo de registros.
    """
    try:
        repo = ApprovalRepository()
        approvals = repo.list_by_status(status=status, limit=limit)
        return {
            "success": True,
            "status": status,
            "count": len(approvals),
            "approvals": [a.to_dict() for a in approvals],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
