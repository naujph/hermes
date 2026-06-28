"""Tool: whatsapp_analyzer — Analisa conversas WhatsApp de leads."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(lead_id: int | None = None, phone: str | None = None) -> dict:
    skill_path = ROOT / "hermes" / "skills" / "whatsapp_analyzer.py"
    payload = {}
    if lead_id:
        payload["lead_id"] = lead_id
    if phone:
        payload["phone"] = phone

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
            return {"success": True, "output": result.get("analysis", {}), "message": result.get("analysis", {}).get("follow_up_suggestion", "")}
        return {"success": False, "error": result.get("errors", ["Erro desconhecido"])}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
