"""SynthesizerAgent — Formata resposta natural a partir dos resultados."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.llm_client import UnifiedLLMClient


class SynthesizerAgent:
    """Transforma resultados de múltiplas tools em resposta útil e natural."""

    def __init__(self, llm: UnifiedLLMClient | None = None):
        self.llm = llm or UnifiedLLMClient(timeout=180)

    def synthesize(
        self,
        user_message: str,
        objective: str,
        executed_steps: list[dict[str, Any]],
        context_text: str = "",
    ) -> str:
        """Gera resposta final em português, direta e prática."""
        prompt = self._build_prompt(user_message, objective, executed_steps, context_text)
        resp = self.llm.complete(prompt, temperature=0.5, max_tokens=2000)
        if resp.error:
            return self._fallback_response(executed_steps)
        return resp.content.strip()

    def _build_prompt(
        self,
        user_message: str,
        objective: str,
        executed_steps: list[dict[str, Any]],
        context_text: str,
    ) -> str:
        steps_text = json.dumps(executed_steps, ensure_ascii=False, indent=2, default=str)
        return (
            "Você é o SynthesizerAgent do Hermes, secretário pessoal do Juan.\n"
            "Sua tarefa é formular uma ÚNICA resposta natural, direta e útil,\n"
            "com base nos resultados das ferramentas que já foram executadas.\n\n"
            f"{context_text}\n\n"
            f"Mensagem do Juan: {user_message}\n"
            f"Objetivo atingido: {objective}\n\n"
            "Resultados das ferramentas:\n"
            f"{steps_text}\n\n"
            "REGRAS DE RESPOSTA:\n"
            "1. Seja direto. Não repita o processo que você fez.\n"
            "2. Use os dados reais dos resultados acima.\n"
            "3. Se houver erro em alguma ferramenta, mencione de forma leve e sugira alternativa.\n"
            "4. Formato adequado para Telegram (curto, com emojis se útil).\n"
            "5. NUNCA prometa rentabilidade.\n"
            "6. Se precisar de aprovação do Juan para alguma ação destrutiva, pergunte claramente.\n"
            "7. Não diga 'não tenho acesso' se os dados estão nos resultados acima.\n"
        )

    def _fallback_response(self, executed_steps: list[dict[str, Any]]) -> str:
        for step in reversed(executed_steps):
            result = step.get("result", {})
            if result.get("success"):
                # Tenta extrair uma mensagem amigável
                for key in ("message", "output", "digest", "briefing", "response"):
                    if key in result:
                        value = result[key]
                        if isinstance(value, str):
                            return value[:1000]
        return "Concluí. Posso te ajudar com mais alguma coisa?"
