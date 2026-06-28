"""Tool: project_manager — Gerencia projetos pessoais."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(action: str = "list", name: str = "", status: str = "", priority: str = "", notes: str = "", project_id: int | None = None) -> dict:
    skill_path = ROOT / "hermes" / "skills" / "project_manager.py"
    payload = {"action": action}
    if name:
        payload["name"] = name
    if status:
        payload["status"] = status
    if priority:
        payload["priority"] = priority
    if notes:
        payload["notes"] = notes
    if project_id:
        payload["project_id"] = project_id

    try:
        proc = subprocess.run(
            [sys.executable, str(skill_path)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=60,
            cwd=str(ROOT),
        )
        if proc.returncode != 0:
            return {"success": False, "error": proc.stderr.decode("utf-8", errors="replace")}
        result = json.loads(proc.stdout.decode("utf-8"))
        if result.get("success"):
            return {"success": True, "output": result.get("message", ""), "projects": result.get("projects", [])}
        return {"success": False, "error": result.get("error", "Erro desconhecido")}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
