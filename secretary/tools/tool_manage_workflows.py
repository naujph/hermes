"""Tool: manage_workflows — CRUD de workflows para o Workflow Studio."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.repositories.workflow_repository import WorkflowRepository
from app.utils.normalizers import dumps_json

logger = logging.getLogger(__name__)


def _parse_definition_json(definition_json: Any) -> dict[str, Any]:
    """Converte definition_json de string/dict para dict normalizado."""
    if definition_json is None:
        return {"nodes": [], "edges": []}
    if isinstance(definition_json, dict):
        return definition_json
    if isinstance(definition_json, str):
        try:
            parsed = json.loads(definition_json)
            return parsed if isinstance(parsed, dict) else {"nodes": [], "edges": []}
        except json.JSONDecodeError as exc:
            logger.warning("definition_json não é JSON válido: %s", exc)
            return {"nodes": [], "edges": []}
    return {"nodes": [], "edges": []}


def execute_manage_workflows(
    action: str,
    name: str | None = None,
    workflow_id: int | None = None,
    definition_json: Any = None,
    description: str | None = None,
    category: str | None = None,
    owner: str = "juan",
) -> dict[str, Any]:
    """Executa ações de CRUD sobre workflows.

    Ações:
    - create: cria um workflow (name e definition_json obrigatórios).
    - update: atualiza workflow existente (workflow_id obrigatório).
    - delete: remove workflow (workflow_id obrigatório).
    - list: lista workflows do owner (templates não inclusos).
    """
    repo = WorkflowRepository()
    repo.ensure_tables()

    action = str(action).strip().lower()

    if action == "create":
        if not name:
            return {"success": False, "error": "Nome é obrigatório para criar workflow."}

        definition = _parse_definition_json(definition_json)
        if not definition.get("nodes"):
            return {"success": False, "error": "definition_json deve conter ao menos um nó."}

        now = datetime.now(UTC).isoformat()
        record = {
            "owner": owner,
            "name": name,
            "description": description or f"Workflow criado via Hermes em {now}",
            "is_template": 0,
            "category": category or "geral",
            "definition_json": definition,
            "status": "draft",
            "created_at": now,
            "updated_at": now,
        }
        try:
            new_id = repo.create_workflow(record)
            return {
                "success": True,
                "workflow_id": new_id,
                "message": f"Workflow '{name}' criado com ID {new_id}.",
                "name": name,
                "category": category or "geral",
            }
        except Exception as exc:
            logger.exception("Erro ao criar workflow")
            return {"success": False, "error": str(exc)}

    if action == "update":
        if not workflow_id:
            return {"success": False, "error": "workflow_id é obrigatório para atualizar."}

        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if category is not None:
            payload["category"] = category
        if definition_json is not None:
            definition = _parse_definition_json(definition_json)
            if not definition.get("nodes"):
                return {"success": False, "error": "definition_json deve conter ao menos um nó."}
            payload["definition_json"] = definition

        if not payload:
            return {"success": False, "error": "Nenhum campo fornecido para atualização."}

        try:
            repo.update_workflow(int(workflow_id), payload)
            return {
                "success": True,
                "workflow_id": int(workflow_id),
                "message": f"Workflow {workflow_id} atualizado.",
            }
        except Exception as exc:
            logger.exception("Erro ao atualizar workflow %s", workflow_id)
            return {"success": False, "error": str(exc)}

    if action == "delete":
        if not workflow_id:
            return {"success": False, "error": "workflow_id é obrigatório para deletar."}

        try:
            repo.delete_workflow(int(workflow_id))
            return {
                "success": True,
                "workflow_id": int(workflow_id),
                "message": f"Workflow {workflow_id} removido.",
            }
        except Exception as exc:
            logger.exception("Erro ao deletar workflow %s", workflow_id)
            return {"success": False, "error": str(exc)}

    if action == "list":
        try:
            workflows = repo.list_workflows(owner=owner, limit=100)
            return {
                "success": True,
                "workflows": workflows,
                "count": len(workflows),
                "message": f"{len(workflows)} workflow(s) encontrado(s).",
            }
        except Exception as exc:
            logger.exception("Erro ao listar workflows")
            return {"success": False, "error": str(exc)}

    return {"success": False, "error": f"Ação desconhecida: {action}"}
