"""Tool: manage_tasks — Gerenciador nativo de tarefas."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TASKS_FILE = ROOT / "context" / "tasks.json"

def _load_tasks() -> list[dict[str, Any]]:
    if not TASKS_FILE.exists():
        return []
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save_tasks(tasks: list[dict[str, Any]]) -> None:
    TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

def execute_manage_tasks(action: str, title: str = "", due_date: str = "", task_id: str = "") -> dict[str, Any]:
    tasks = _load_tasks()
    
    if action == "add":
        if not title:
            return {"success": False, "error": "O título é obrigatório para criar uma tarefa."}
        
        new_task = {
            "id": uuid.uuid4().hex[:8],
            "title": title,
            "due_date": due_date,
            "status": "pending",
            "created_at": datetime.now(UTC).isoformat()
        }
        tasks.append(new_task)
        _save_tasks(tasks)
        return {
            "success": True, 
            "message": f"Tarefa '{title}' adicionada com sucesso. ID: {new_task['id']}"
        }
        
    elif action == "list":
        pending = [t for t in tasks if t.get("status") == "pending"]
        if not pending:
            return {"success": True, "message": "Nenhuma tarefa pendente no momento."}
            
        lines = ["Tarefas Pendentes:"]
        for t in pending:
            due = f" (Para: {t['due_date']})" if t.get("due_date") else ""
            lines.append(f"- [{t['id']}] {t['title']}{due}")
            
        return {"success": True, "message": "\n".join(lines)}
        
    elif action == "complete":
        if not task_id:
            return {"success": False, "error": "Forneça o task_id para concluir a tarefa."}
            
        found = False
        for t in tasks:
            if t["id"] == task_id or t["id"].startswith(task_id):
                t["status"] = "completed"
                t["completed_at"] = datetime.now(UTC).isoformat()
                found = True
                break
                
        if not found:
            return {"success": False, "error": f"Tarefa {task_id} não encontrada."}
            
        _save_tasks(tasks)
        return {"success": True, "message": f"Tarefa {task_id} marcada como concluída."}
        
    elif action == "delete":
        if not task_id:
            return {"success": False, "error": "Forneça o task_id para deletar a tarefa."}
            
        initial_len = len(tasks)
        tasks = [t for t in tasks if t["id"] != task_id and not t["id"].startswith(task_id)]
        
        if len(tasks) == initial_len:
            return {"success": False, "error": f"Tarefa {task_id} não encontrada."}
            
        _save_tasks(tasks)
        return {"success": True, "message": f"Tarefa {task_id} deletada."}
        
    return {"success": False, "error": f"Ação desconhecida: {action}"}
