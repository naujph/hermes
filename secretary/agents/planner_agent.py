"""PlannerAgent — Decide objetivo e sequência de ferramentas."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.llm_client import UnifiedLLMClient
from hermes.secretary.tools.registry import get_registry_text


ALLOWED_TOOLS = (
    "direct_response, query_db, search_alerts, run_skill, add_lead, enrich_lead, "
    "whatsapp_analyzer, cancel_meeting, update_memory, create_alert, manage_tasks, "
    "draft_email, web_search, portfolio_builder, project_manager, agent_council, "
    "generate_code, run_code, save_video_summary, manage_workflows, run_workflow"
)

PLANNER_SCHEMA = {
    "objective": "string (o que precisa ser resolvido)",
    "steps": [
        {
            "step_number": "int",
            "tool": "nome da ferramenta",
            "args": "dict com parâmetros",
            "reason": "por que esse passo",
            "depends_on": "list[int] (opcional)",
        }
    ],
    "needs_confirmation": "boolean (true se precisa perguntar antes de executar)",
    "confirmation_message": "string (opcional)",
}


class PlannerAgent:
    """Gera plano de execução baseado na mensagem do usuário e contexto."""

    def __init__(self, llm: UnifiedLLMClient | None = None):
        self.llm = llm or UnifiedLLMClient(timeout=180)

    def plan(
        self,
        user_message: str,
        context_text: str,
        available_tools_text: str = "",
    ) -> dict[str, Any]:
        """Retorna plano em JSON."""
        prompt = self._build_prompt(user_message, context_text, available_tools_text)
        result = self.llm.extract_json(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2000,
            schema_hint=PLANNER_SCHEMA,
        )
        parsed = result.get("parsed")
        if not parsed or not isinstance(parsed, dict):
            return self._fallback_plan(user_message)

        steps = parsed.get("steps", [])
        if not steps:
            return self._fallback_plan(user_message)

        # Normaliza steps
        for i, step in enumerate(steps):
            step["step_number"] = i + 1
            step.setdefault("depends_on", [])
            step.setdefault("reason", "")
            step.setdefault("status", "pending")
            step.setdefault("result", None)

        return {
            "objective": parsed.get("objective", "Responder Juan"),
            "steps": steps,
            "needs_confirmation": bool(parsed.get("needs_confirmation", False)),
            "confirmation_message": parsed.get("confirmation_message", ""),
        }

    def _build_prompt(self, user_message: str, context_text: str, tools_text: str) -> str:
        return (
            "Você é o PlannerAgent do Hermes, secretário operacional do Juan.\n"
            "Seu trabalho é analisar o pedido do Juan e produzir um PLANO DE EXECUÇÃO\n"
            "com uma sequência de ferramentas.\n\n"
            f"{context_text}\n\n"
            f"{tools_text}\n\n"
            "REGRAS ABSOLUTAS (violar qualquer uma gera plano inútil):\n"
            f"1. Você só pode usar estas ferramentas EXATAS: {ALLOWED_TOOLS}.\n"
            "2. NUNCA invente nomes fora dessa lista. Não existe 'search_obsidian', 'search_memory', 'search_crm', 'search_calendar', 'ask_user', 'search_telegram_history', 'find_notes'.\n"
            "3. Para buscar resumos, notas, briefings, alertas ou conhecimento, use SEMPRE search_alerts ou query_db.\n"
            "4. Cada passo deve ter: step_number, tool, args, reason.\n"
            "5. Se um passo depende do resultado de outro, use depends_on com números de step.\n"
            "6. NÃO invente parâmetros que não estão na mensagem ou no contexto.\n"
            "7. Para ações destrutivas (cancelar, deletar, enviar mensagem), defina needs_confirmation=true.\n"
            "8. Se o pedido for simples ou conversa casual, use apenas direct_response.\n"
            "9. Se precisar de múltiplas perspectivas, use agent_council.\n"
            "10. Sempre busque na memória/contexto antes de assumir que não sabe.\n"
            "11. NUNCA prometa rentabilidade. Respeite CVM/XP.\n"
            "12. Se precisar perguntar algo ao Juan, use direct_response como último passo com a pergunta no campo text.\n"
            "13. Se não souber o que fazer, use direct_response e peça esclarecimento.\n\n"
            "Pense passo a passo. Seja específico nos argumentos. Escolha a ferramenta correta da lista.\n\n"
            f"Mensagem do Juan: {user_message}\n\n"
            "Responda APENAS com o JSON do plano."
        )

    def _fallback_plan(self, user_message: str) -> dict[str, Any]:
        return {
            "objective": "Responder Juan",
            "steps": [
                {
                    "step_number": 1,
                    "tool": "direct_response",
                    "args": {"text": "Recebi sua mensagem. Posso ajudar com isso?"},
                    "reason": "Plano não gerado; resposta de fallback.",
                    "depends_on": [],
                    "status": "pending",
                    "result": None,
                }
            ],
            "needs_confirmation": False,
            "confirmation_message": "",
        }
