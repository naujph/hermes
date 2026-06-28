"""Tool: run_workflow — Inicia e controla execuções de workflows."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.workflow_engine import WorkflowEngine, WorkflowEngineError

logger = logging.getLogger(__name__)


def _parse_context_json(context_json: Any) -> dict[str, Any]:
    """Converte context_json de string/dict para dict."""
    if context_json is None:
        return {}
    if isinstance(context_json, dict):
        return context_json
    if isinstance(context_json, str):
        try:
            parsed = json.loads(context_json)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _find_latest_run(engine: WorkflowEngine, workflow_id: int, owner: str = "juan") -> int | None:
    """Retorna o ID da execução mais recente de um workflow."""
    try:
        runs = engine.repo.list_runs(workflow_id=workflow_id, owner=owner, limit=1)
        if runs:
            return runs[0]["id"]
    except Exception as exc:
        logger.warning("Erro ao buscar runs do workflow %s: %s", workflow_id, exc)
    return None


def execute_run_workflow(
    action: str,
    workflow_id: int | None = None,
    context_json: Any = "{}",
    owner: str = "juan",
) -> dict[str, Any]:
    """Controla execução assistida de workflows.

    Ações:
    - start: inicia uma nova execução do workflow informado (workflow_id = workflow_id).
    - next: executa o nó atual da execução ativa (workflow_id = run_id, ou busca a mais recente).
    - approve: aprova o nó human_approval atual e continua (workflow_id = run_id).
    - cancel: cancela a execução (workflow_id = run_id).
    """
    engine = WorkflowEngine()
    action = str(action).strip().lower()
    context = _parse_context_json(context_json)

    if action == "start":
        if not workflow_id:
            return {"success": False, "error": "workflow_id é obrigatório para iniciar."}

        run_id_from_context = context.pop("run_id", None)
        try:
            run = engine.start_run(
                workflow_id=int(workflow_id),
                initial_context=context,
                owner=owner,
            )
            run_id = run["run_id"]
            # Executa o nó start automaticamente para avançar ao primeiro nó real
            result = engine.execute_node(run_id)
            return {
                "success": True,
                "run_id": run_id,
                "workflow_id": int(workflow_id),
                "status": result.get("status"),
                "current_node_id": result.get("next_node_id") or result.get("node_id"),
                "current_node_type": result.get("next_node_type"),
                "output": result.get("output"),
                "message": result.get("message", "Execução iniciada."),
            }
        except WorkflowEngineError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            logger.exception("Erro ao iniciar workflow %s", workflow_id)
            return {"success": False, "error": str(exc)}

    if action == "next":
        run_id = context.pop("run_id", None) or workflow_id
        if not run_id:
            return {"success": False, "error": "run_id ou workflow_id é obrigatório para executar próximo nó."}

        try:
            run_id = int(run_id)
        except (TypeError, ValueError):
            return {"success": False, "error": f"run_id inválido: {run_id}"}

        # Se workflow_id foi passado em vez de run_id, tenta achar a execução mais recente
        if run_id == workflow_id:
            latest = _find_latest_run(engine, int(workflow_id), owner=owner)
            if latest:
                run_id = latest
            else:
                return {"success": False, "error": f"Nenhuma execução encontrada para workflow {workflow_id}."}

        try:
            result = engine.execute_node(run_id)
            return {
                "success": True,
                "run_id": run_id,
                "workflow_id": result.get("workflow_id"),
                "status": result.get("status"),
                "current_node_id": result.get("node_id"),
                "current_node_type": result.get("node_type"),
                "next_node_id": result.get("next_node_id"),
                "next_node_type": result.get("next_node_type"),
                "output": result.get("output"),
                "message": result.get("message", "Nó executado."),
            }
        except WorkflowEngineError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            logger.exception("Erro ao executar próximo nó do run %s", run_id)
            return {"success": False, "error": str(exc)}

    if action == "approve":
        run_id = context.pop("run_id", None) or workflow_id
        if not run_id:
            return {"success": False, "error": "run_id ou workflow_id é obrigatório para aprovar."}

        try:
            run_id = int(run_id)
        except (TypeError, ValueError):
            return {"success": False, "error": f"run_id inválido: {run_id}"}

        if run_id == workflow_id:
            # Busca execução pausada mais recente do workflow
            try:
                runs = engine.repo.list_runs(workflow_id=int(workflow_id), owner=owner, limit=50)
                paused = [r for r in runs if r.get("status") == "paused"]
                if paused:
                    run_id = paused[0]["id"]
                else:
                    return {"success": False, "error": f"Nenhuma execução pausada para workflow {workflow_id}."}
            except Exception as exc:
                return {"success": False, "error": f"Erro ao buscar execução pausada: {exc}"}

        approval_payload = context.pop("approval_payload", None) or {}
        try:
            result = engine.approve_and_continue(run_id, approval_payload=approval_payload)
            return {
                "success": True,
                "run_id": run_id,
                "workflow_id": result.get("workflow_id"),
                "status": result.get("status"),
                "current_node_id": result.get("next_node_id") or result.get("node_id"),
                "current_node_type": result.get("next_node_type"),
                "output": result.get("output"),
                "message": result.get("message", "Aprovação registrada."),
            }
        except WorkflowEngineError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            logger.exception("Erro ao aprovar run %s", run_id)
            return {"success": False, "error": str(exc)}

    if action == "cancel":
        run_id = context.pop("run_id", None) or workflow_id
        if not run_id:
            return {"success": False, "error": "run_id ou workflow_id é obrigatório para cancelar."}

        try:
            run_id = int(run_id)
        except (TypeError, ValueError):
            return {"success": False, "error": f"run_id inválido: {run_id}"}

        if run_id == workflow_id:
            # Cancela a execução mais recente ativa/pausada do workflow
            try:
                runs = engine.repo.list_runs(workflow_id=int(workflow_id), owner=owner, limit=50)
                active = [r for r in runs if r.get("status") in ("running", "paused")]
                if active:
                    run_id = active[0]["id"]
                else:
                    return {"success": False, "error": f"Nenhuma execução ativa para workflow {workflow_id}."}
            except Exception as exc:
                return {"success": False, "error": f"Erro ao buscar execução ativa: {exc}"}

        try:
            result = engine.cancel_run(run_id)
            return {
                "success": True,
                "run_id": run_id,
                "status": result.get("status"),
                "message": result.get("message", "Execução cancelada."),
            }
        except WorkflowEngineError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            logger.exception("Erro ao cancelar run %s", run_id)
            return {"success": False, "error": str(exc)}

    return {"success": False, "error": f"Ação desconhecida: {action}"}
