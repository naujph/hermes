"""Tool: resolve_approval — Aprova ou rejeita um pedido do Hermes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_env
from app.repositories.approval_repository import ApprovalRepository
from hermes.secretary.tools.registry import get_tool

load_env()


def resolve_approval(approval_id: int, resolution: str, execute: bool = True) -> dict:
    """Aprova/rejeita um pending_approval. Se aprovado e execute=True, executa o draft_payload.

    Args:
        approval_id: ID do pending_approval.
        resolution: 'approved' ou 'rejected'.
        execute: se True (padrao), executa a acao aprovada automaticamente.
    """
    if resolution not in ("approved", "rejected"):
        return {"success": False, "error": "resolution deve ser 'approved' ou 'rejected'."}

    repo = ApprovalRepository()
    approval = repo.get_by_id(approval_id)
    if not approval:
        return {"success": False, "error": f"Aprovacao {approval_id} nao encontrada."}

    if approval.status != "pending":
        return {"success": False, "error": f"Aprovacao ja esta {approval.status}."}

    resolved = repo.resolve(approval_id, resolution)
    if not resolved:
        return {"success": False, "error": "Falha ao atualizar aprovacao."}

    if resolution == "rejected":
        return {
            "success": True,
            "message": f"Aprovacao {approval_id} rejeitada.",
            "approval": resolved.to_dict(),
            "executed": False,
        }

    if not execute:
        return {
            "success": True,
            "message": f"Aprovacao {approval_id} aprovada, mas nao executada (execute=False).",
            "approval": resolved.to_dict(),
            "executed": False,
        }

    # Executa o draft_payload
    draft = approval.draft_payload or {}
    tool_name = draft.get("tool")
    args = draft.get("args", {})

    if not tool_name:
        return {
            "success": True,
            "message": f"Aprovacao {approval_id} aprovada, mas sem tool no draft_payload.",
            "approval": resolved.to_dict(),
            "executed": False,
        }

    try:
        # Importa HermesCore para executar tool interna
        from hermes.secretary.core_v2 import HermesCore
        core = HermesCore()
        result = core._execute_tool_internal(tool_name, args)
        repo.mark_executed(approval_id)
        return {
            "success": result.get("success", True),
            "message": f"Aprovacao {approval_id} aprovada e executada.",
            "approval": resolved.to_dict(),
            "executed": True,
            "result": result,
        }
    except Exception as exc:
        return {
            "success": False,
            "message": f"Aprovacao aprovada, mas execucao falhou: {exc}",
            "approval": resolved.to_dict(),
            "executed": False,
            "error": str(exc),
        }
