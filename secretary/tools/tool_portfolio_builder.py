"""Tool: portfolio_builder — Análise qualitativa de carteira pessoal."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(carteira: list[dict] | None = None, acao: str = "alertas") -> dict:
    """
    acao: alertas | teses | rebalancear
    carteira: [{"ticker": str, "qtd": int, "pm": float, "setor": str, "notas": str}, ...]
    """
    skill_path = ROOT / "hermes" / "skills" / "portfolio_builder.py"
    payload = {"acao": acao}
    if carteira:
        payload["carteira"] = carteira

    try:
        proc = subprocess.run(
            [sys.executable, str(skill_path)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=120,
            cwd=str(ROOT),
        )
        if proc.returncode != 0:
            return {"success": False, "error": proc.stderr.decode("utf-8", errors="replace")}
        result = json.loads(proc.stdout.decode("utf-8"))
        if result.get("success"):
            # Extrai o campo relevante
            output = result.get("analysis") or result.get("theses") or result.get("rebalance_plan") or result.get("alerts", [])
            return {"success": True, "output": output, "raw": result}
        return {"success": False, "error": result.get("errors", ["Erro desconhecido"])}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
