"""ApprovalAgent (tambem chamado Critic / AutonomyGate).

Avalia se um plano ou passo do Hermes pode ser executado automaticamente
ou se precisa de aprovacao humana. Regras sao majoritariamente deterministicas;
o LLM e usado apenas para casos de duvida.
"""
from __future__ import annotations

from typing import Any

from app.llm_client import UnifiedLLMClient


# Categorias de acoes e seu nivel de autonomia.
# "auto" = executa sem perguntar.
# "approval" = cria pending_approval.
# "ask" = pergunta no chat (confirmacao rapida, nao persiste como approval).
AUTO_ACTIONS = {
    # Leitura e analise
    "query_db",
    "search_alerts",
    "direct_response",
    "whatsapp_analyzer",
    "agent_council",
    # Memoria e organizacao interna
    "update_memory",
    "create_alert",
    "manage_tasks",
    "project_manager",
    "save_video_summary",
    "add_lead",
    "enrich_lead",
    # Rascunhos e geracao local
    "draft_email",
    "generate_code",
    "portfolio_builder",
    "web_search",
}

APPROVAL_ACTIONS = {
    # Envio externo
    "send_message",
    "send_email",
    "send_whatsapp",
    "send_sms",
    # Agenda destrutiva
    "cancel_meeting",
    # Operacao comercial
    "update_pipeline",
    "create_opportunity",
    "register_revenue",
    "register_commission",
    "mark_converted",
    "mark_lost",
    # Acao em lote que toca terceiros
    "run_workflow",
    "run_code",
}


class ApprovalAgent:
    """Gatekeeper de autonomia do Hermes."""

    def __init__(self, llm: UnifiedLLMClient | None = None):
        self.llm = llm or UnifiedLLMClient()

    def evaluate(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Avalia um passo/tool e decide se pode executar automaticamente.

        Retorna:
            {
                "autonomous": bool,
                "approval_required": bool,
                "reason": str,
                "title": str,              # titulo do card de aprovacao (se approval)
                "description": str,        # descricao (se approval)
                "draft_payload": dict,     # payload exato para reexecucao (se approval)
            }
        """
        context = context or {}
        action_type = self._classify(tool_name, args)

        if action_type == "auto":
            return {
                "autonomous": True,
                "approval_required": False,
                "reason": f"'{tool_name}' e uma acao de leitura, analise, memoria ou rascunho. Executa automaticamente.",
                "title": "",
                "description": "",
                "draft_payload": {},
            }

        if action_type == "approval":
            title, description = self._build_approval_card(tool_name, args, context)
            return {
                "autonomous": False,
                "approval_required": True,
                "reason": f"'{tool_name}' pode afetar terceiros ou executar operacao comercial. Requer aprovacao humana.",
                "title": title,
                "description": description,
                "draft_payload": {"tool": tool_name, "args": args},
            }

        # Caso de duvida: consulta LLM
        return self._llm_decide(tool_name, args, context)

    def _classify(self, tool_name: str, args: dict[str, Any]) -> str:
        """Classifica a acao em 'auto', 'approval' ou 'uncertain'."""
        if tool_name in AUTO_ACTIONS:
            return "auto"
        if tool_name in APPROVAL_ACTIONS:
            return "approval"

        # run_skill precisa de analise pelo nome da skill + payload
        if tool_name == "run_skill":
            return self._classify_run_skill(args)

        return "uncertain"

    def _classify_run_skill(self, args: dict[str, Any]) -> str:
        skill_name = args.get("skill_name", "")
        payload = args.get("payload", {})

        # Skills puramente analiticas/organizacionais = auto
        read_only_skills = {
            "daily_digest",
            "market_monitor",
            "cockpit_digest",
            "suggest_follow_up",
            "generate_briefing",
            "whatsapp_analyzer",
            "audio_transcribe",
            "agent_council",
            "vision",
        }
        if skill_name in read_only_skills:
            return "auto"

        # Enriquecimento e organizacao = auto
        if skill_name in {"enrich_lead", "create_alert", "project_manager"}:
            return "auto"

        # lead_finder: auto quando preview; approval quando auto_save
        if skill_name == "lead_finder":
            return "approval" if payload.get("auto_save") else "auto"

        # video: analise auto; renderizar corte e salvar minuta precisam de atencao
        if skill_name == "video":
            action = payload.get("action", "")
            if action in {"render_cuts", "generate_and_render_cuts"}:
                return "approval"
            return "auto"

        # workflow_assistant: depende da acao
        if skill_name == "workflow_assistant":
            action = payload.get("action", "")
            if action in {"execute", "run", "send"}:
                return "approval"
            return "auto"

        # portfolio_builder: recomendacao de ordem real = approval
        if skill_name == "portfolio_builder":
            tesis_type = payload.get("tesis_type", "")
            if "ordem" in str(tesis_type).lower() or "rebalance" in str(tesis_type).lower():
                return "approval"
            return "auto"

        return "uncertain"

    def _build_approval_card(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[str, str]:
        """Monta titulo e descricao human-readable para o card de aprovacao."""
        user_msg = context.get("user_message", "")

        if tool_name == "run_skill" and args.get("skill_name") == "lead_finder":
            payload = args.get("payload", {})
            return (
                "Salvar leads no CRM",
                f"Hermes quer salvar {payload.get('max_results', 10)} leads de {payload.get('segment', 'segmento')} em "
                f"{payload.get('city', '')}-{payload.get('state', '')}. Aprovacao para insercao em lote.",
            )

        if tool_name == "cancel_meeting":
            return (
                "Cancelar reunião",
                f"Motivo: {args.get('reason', 'não informado')}. ID: {args.get('meeting_id', args.get('title_fragment', '?'))}",
            )

        if tool_name in ("send_message", "send_email", "send_whatsapp", "send_sms"):
            return (
                f"Enviar {tool_name.replace('send_', '')}",
                f"Destinatário: {args.get('recipient', args.get('phone', args.get('to', '?')))}\n"
                f"Rascunho: {args.get('body', args.get('message', ''))[:200]}",
            )

        if tool_name == "run_workflow":
            return (
                "Executar workflow",
                f"Workflow/run ID: {args.get('workflow_id', '?')}. Ação: {args.get('action', 'start')}.",
            )

        if tool_name == "run_code":
            return (
                "Executar código Python",
                f"Hermes quer rodar código externo. Caminho: {args.get('file_path', 'inline')}",
            )

        # Fallback generico
        return (
            f"Ação '{tool_name}' precisa de aprovação",
            f"Solicitado no contexto: {user_msg[:120]}...\nPayload: {str(args)[:200]}",
        )

    def _llm_decide(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Para casos de duvida, pergunta ao LLM se a acao e autonoma.

        O LLM deve responder apenas JSON com chaves: autonomous (bool), reason (str).
        Se o LLM falhar, fallback seguro: aproval_required=true.
        """
        user_msg = context.get("user_message", "")
        prompt = (
            "Você é o gatekeeper de autonomia do Hermes, assistente de assessoria financeira.\n"
            "Regras de seguranca:\n"
            "- AUTO: leitura, analise, resumo, busca, memorizacao, criar alertas internos, rascunhos nao enviados.\n"
            "- APROVACAO_HUMANA: enviar mensagem/email/WhatsApp, cancelar/compromissos, executar codigo, operacoes comerciais, alterar status de lead para convertido/perdido, movimentar carteira, salvar leads em lote, renderizar/publicar conteudo.\n"
            "\nDecida se a seguinte acao pode ser executada automaticamente:\n"
            f"Tool: {tool_name}\n"
            f"Args: {args}\n"
            f"Contexto do usuario: {user_msg[:300]}\n\n"
            "Responda APENAS com JSON valido: {\"autonomous\": true/false, \"reason\": \"...\"}\n"
            "Se tiver duvida, prefira exigir aprovacao humana."
        )

        try:
            result = self.llm.extract_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500,
            )
            parsed = result.get("parsed") or {}
            autonomous = bool(parsed.get("autonomous", False))

            if autonomous:
                return {
                    "autonomous": True,
                    "approval_required": False,
                    "reason": parsed.get("reason", "LLM classificou como autonomo."),
                    "title": "",
                    "description": "",
                    "draft_payload": {},
                }

            title, description = self._build_approval_card(tool_name, args, context)
            return {
                "autonomous": False,
                "approval_required": True,
                "reason": parsed.get("reason", "LLM classificou como que requer aprovacao."),
                "title": title,
                "description": description,
                "draft_payload": {"tool": tool_name, "args": args},
            }

        except Exception:
            # Fallback seguro: exige aprovacao
            title, description = self._build_approval_card(tool_name, args, context)
            return {
                "autonomous": False,
                "approval_required": True,
                "reason": "Classificacao automatica nao conseguiu decidir; fallback seguro exige aprovacao humana.",
                "title": title,
                "description": description,
                "draft_payload": {"tool": tool_name, "args": args},
            }
