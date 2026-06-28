"""Tool: market_monitor — Gera alertas proativos de mercado."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run() -> dict:
    skill_path = ROOT / "hermes" / "skills" / "market_monitor.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(skill_path)],
            input=b"{}",
            capture_output=True,
            timeout=60,
            cwd=str(ROOT),
        )
        if proc.returncode != 0:
            return {"success": False, "error": proc.stderr.decode("utf-8", errors="replace")}
        result = json.loads(proc.stdout.decode("utf-8"))
        if result.get("success"):
            return {"success": True, "output": result.get("message", ""), "alerts": result.get("alerts", [])}
        return {"success": False, "error": result.get("error", "Erro desconhecido")}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
