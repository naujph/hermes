#!/usr/bin/env python3
"""Skill: project_manager

Gerenciador de projetos pessoais do Juan.
Cria, lista, atualiza e conclui projetos salvos no banco SQLite.

Nova tabela: personal_projects (criada automaticamente)
Recebe JSON via stdin, retorna JSON via stdout.

Uso:
    echo '{"action": "create", "name": "Consórcio terreno", "notes": "Lance em 12 meses"}' | python hermes/skills/project_manager.py
    echo '{"action": "list"}' | python hermes/skills/project_manager.py
    echo '{"action": "update", "project_id": 1, "status": "em_andamento"}' | python hermes/skills/project_manager.py
"""
import json
import sys
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection


SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'ativo',
    priority TEXT DEFAULT 'media',
    notes TEXT,
    due_date TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def ensure_schema() -> None:
    with get_connection() as conn:
        conn.execute(SCHEMA)


def create_project(name: str, status: str = "ativo", priority: str = "media", notes: str = "", due_date: str = "") -> dict:
    ensure_schema()
    now = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO personal_projects (name, status, priority, notes, due_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, status, priority, notes, due_date, now, now),
        )
        return {"success": True, "project_id": cursor.lastrowid, "message": f"Projeto '{name}' criado."}


def list_projects(status: str | None = None) -> dict:
    ensure_schema()
    with get_connection() as conn:
        if status:
            rows = conn.execute("SELECT * FROM personal_projects WHERE status = ? ORDER BY priority DESC, created_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM personal_projects ORDER BY priority DESC, created_at DESC").fetchall()

    projects = [dict(r) for r in rows]
    if not projects:
        return {"success": True, "projects": [], "message": "Nenhum projeto encontrado."}

    lines = ["📋 Projetos pessoais:"]
    for p in projects:
        emoji = {"ativo": "🟢", "em_andamento": "🟡", "concluido": "✅", "pausado": "⏸️", "cancelado": "❌"}.get(p["status"], "⚪")
        lines.append(f"  {emoji} {p['name']} ({p['status']}) — prioridade: {p['priority']}")
        if p["notes"]:
            lines.append(f"     📝 {p['notes']}")
        if p["due_date"]:
            lines.append(f"     📅 Prazo: {p['due_date']}")

    return {"success": True, "projects": projects, "message": "\n".join(lines)}


def update_project(project_id: int, **kwargs) -> dict:
    ensure_schema()
    now = datetime.now(UTC).isoformat()
    fields = {k: v for k, v in kwargs.items() if v is not None}
    if not fields:
        return {"success": False, "error": "Nenhum campo para atualizar."}

    fields["updated_at"] = now
    assignments = ", ".join([f"{k} = ?" for k in fields])
    values = list(fields.values()) + [project_id]

    with get_connection() as conn:
        cursor = conn.execute(f"UPDATE personal_projects SET {assignments} WHERE id = ?", values)
        if cursor.rowcount == 0:
            return {"success": False, "error": f"Projeto {project_id} não encontrado."}

    return {"success": True, "message": f"Projeto {project_id} atualizado."}


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "errors": ["JSON inválido"]}))
        sys.exit(1)

    action = payload.get("action", "").lower()

    if action == "create":
        result = create_project(
            name=payload.get("name", ""),
            status=payload.get("status", "ativo"),
            priority=payload.get("priority", "media"),
            notes=payload.get("notes", ""),
            due_date=payload.get("due_date", ""),
        )
    elif action == "list":
        result = list_projects(status=payload.get("status"))
    elif action == "update":
        pid = payload.get("project_id")
        if not pid:
            print(json.dumps({"success": False, "error": "project_id obrigatório."}))
            sys.exit(1)
        result = update_project(
            project_id=pid,
            status=payload.get("status"),
            priority=payload.get("priority"),
            notes=payload.get("notes"),
            due_date=payload.get("due_date"),
        )
    else:
        result = {"success": False, "error": f"Ação '{action}' inválida. Use: create, list, update."}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
