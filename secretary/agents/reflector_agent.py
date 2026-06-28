"""ReflectorAgent — Decide se o objetivo foi atingido ou se precisa de mais passos."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.llm_client import UnifiedLLMClient


REFLECTOR_SCHEMA = {
    "objective_achieved": "boolean",
    "needs_more_steps": "boolean",
    "additional_steps": [
        {
            "tool": "string",
            "args": "dict",
            "reason": "string",
        }
    ],
    "notes": "string",
}


class ReflectorAgent:
    """Avalia resultados parciais e decide continuar ou parar."""

    def __init__(self, llm: UnifiedLLMClient | None = None):
        self.llm = llm or UnifiedLLMClient(timeout=120)

    def evaluate(
        self,
        user_message: str,
        objective: str,
        executed_steps: list[dict[str, Any]],
        context_text: str = "",
    ) -> dict[str, Any]:
        """Retorna decisão sobre continuar ou finalizar."""
        prompt = self._build_prompt(user_message, objective, executed_steps, context_text)
        result = self.llm.extract_json(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1500,
            schema_hint=REFLECTOR_SCHEMA,
        )
        parsed = result.get("parsed")
        if not parsed or not isinstance(parsed, dict):
            return {"objective_achieved": True, "needs_more_steps": False, "additional_steps": [], "notes": ""}

        return {
            "objective_achieved": bool(parsed.get("objective_achieved", True)),
            "needs_more_steps": bool(parsed.get("needs_more_steps", False)),
            "additional_steps": parsed.get("additional_steps", []) or [],
            "notes": parsed.get("notes", ""),
        }

    def _build_prompt(
        self,
        user_message: str,
        objective: str,
        executed_steps: list[dict[str, Any]],
        context_text: str,
    ) -> str:
        steps_text = json.dumps(executed_steps, ensure_ascii=False, indent=2, default=str)
        return (
            "Você é o ReflectorAgent do Hermes.\n"
            "Avalie se o objetivo foi atingido com base nos passos executados.\n"
            "Se não, sugira passos adicionais SOMENTE se necessário.\n\n"
            f"{context_text}\n\n"
            f"Objetivo: {objective}\n"
            f"Mensagem original do Juan: {user_message}\n\n"
            "Passos executados:\n"
            f"{steps_text}\n\n"
            "REGRAS:\n"
            "1. objective_achieved = true se a pergunta/pedido do Juan foi respondido.\n"
            "2. needs_more_steps = true se faltou buscar dados importantes.\n"
            "3. Se needs_more_steps=true, liste additional_steps com tool e args concretos.\n"
            "4. Se um passo falhou, avalie se é possível seguir sem ele ou se precisa de alternativa.\n"
            "5. Seja econômico: não peça passos desnecessários.\n\n"
            "Responda APENAS com JSON."
        )
