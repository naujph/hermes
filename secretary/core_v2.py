#!/usr/bin/env python3
"""Hermes Secretary Core v2 — Orquestrador multi-agente autônomo.

Arquitetura:
1. ContextAgent coleta memória pessoal, memória convexa, snapshot e histórico.
2. Keyword shortcut resolve comandos óbvios rapidamente.
3. PlannerAgent decide objetivo e sequência de ferramentas.
4. ExecutorAgent executa o plano passo a passo.
5. ReflectorAgent avalia se precisa de mais passos (até 3 ciclos).
6. SynthesizerAgent formula resposta natural.
7. Persiste conversa na memória convexa e histórico pessoal.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_env
from app.llm_client import UnifiedLLMClient
from hermes.secretary.context.personal_memory import PersonalMemory
from hermes.secretary.agents import (
    ContextAgent,
    PlannerAgent,
    ExecutorAgent,
    ReflectorAgent,
    SynthesizerAgent,
)
from hermes.secretary.agents.approval_agent import ApprovalAgent
from hermes.secretary.tools.registry import get_registry_text, get_tool
from hermes.memory.ingestor import Ingestor
from hermes.memory.background_runner import BackgroundRunner
from app.repositories.approval_repository import ApprovalRepository
from app.database import init_db

load_env()


class HermesCore:
    """Orquestrador central do Secretário Hermes — v2 multi-agente."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_history: int = 10,
        max_plan_cycles: int = 3,
    ):
        self.llm = UnifiedLLMClient(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=180,
        )
        self.memory = PersonalMemory()
        self.background = BackgroundRunner()
        self.ingestor = Ingestor(background=self.background)
        self.max_history = max_history
        self.max_plan_cycles = max_plan_cycles
        self.history: list[dict[str, Any]] = []
        self.last_lead_finder_result: dict[str, Any] | None = None
        self.last_video_result: dict[str, Any] | None = None
        self._last_user_message: str = ""
        self._last_source: str = "hermes_core"

        self.context_agent = ContextAgent(memory=self.memory)
        self.planner = PlannerAgent(llm=self.llm)
        self.executor = ExecutorAgent(tool_runner=self._execute_tool_gated)
        self.reflector = ReflectorAgent(llm=self.llm)
        self.synthesizer = SynthesizerAgent(llm=self.llm)
        self.approval_agent = ApprovalAgent(llm=self.llm)
        self.approval_repo = ApprovalRepository()

        init_db()  # Garante tabelas, incluindo pending_approvals
        self.background.start()

    # ── Public API ───────────────────────────────────────────────────

    def process_message(self, user_message: str, source: str = "hermes_core") -> dict[str, Any]:
        try:
            return self._process(user_message, source=source)
        except Exception as exc:
            traceback.print_exc()
            return {
                "success": False,
                "error": str(exc),
                "response": "❌ Algo deu errado internamente. Tente novamente.",
            }

    def _process(self, user_message: str, source: str = "hermes_core") -> dict[str, Any]:
        self._last_user_message = user_message
        self._last_source = source

        # 1. Coleta contexto
        context = self.context_agent.gather(user_message, self.history)
        context_text = self.context_agent.to_prompt(context)

        # 2. Keyword shortcut para comandos óbvios
        shortcut = self._detect_intent(user_message)
        if shortcut:
            # Converte shortcut em plano de 1 passo
            plan = {
                "objective": f"Executar comando: {shortcut['tool']}",
                "steps": [
                    {
                        "step_number": 1,
                        "tool": shortcut["tool"],
                        "args": shortcut.get("args", {}),
                        "reason": "Comando direto detectado por keyword.",
                        "depends_on": [],
                        "status": "pending",
                        "result": None,
                    }
                ],
                "needs_confirmation": False,
                "confirmation_message": "",
            }
        else:
            # 3. Planner decide o plano
            tools_text = get_registry_text()
            plan = self.planner.plan(user_message, context_text, tools_text)

        # Filtra passos com tools desconhecidas
        plan["steps"] = self._filter_valid_steps(plan.get("steps", []))

        # Se o planner gerou plano inválido (muitas tools fora da lista), tenta atalho de busca
        valid_steps = [s for s in plan["steps"] if get_tool(s.get("tool", ""))]
        if len(valid_steps) == 0 or (len(plan["steps"]) > 2 and len(valid_steps) <= len(plan["steps"]) // 2):
            search_intent = self._detect_search_intent(user_message)
            if search_intent:
                plan = {
                    "objective": "Buscar informações no CRM/memória",
                    "steps": [
                        {
                            "step_number": 1,
                            "tool": "search_alerts",
                            "args": {"query": search_intent, "limit": 10},
                            "reason": "Planner gerou tools inválidas; fallback para busca em alertas.",
                            "depends_on": [],
                            "status": "pending",
                            "result": None,
                        }
                    ],
                    "needs_confirmation": False,
                    "confirmation_message": "",
                }

        # Se precisa de confirmação, interrompe e pergunta
        if plan.get("needs_confirmation") and plan.get("confirmation_message"):
            response = plan["confirmation_message"]
            self._update_history(user_message, response, "direct_response")
            self.ingestor.ingest_conversation(user_message, response, topic="confirmacao")
            return {"success": True, "response": response, "tool_used": "direct_response"}

        # 4. Ciclo de execução + reflexão
        executed_steps: list[dict[str, Any]] = []
        cycle = 0
        while cycle < self.max_plan_cycles:
            cycle += 1

            steps = plan.get("steps", [])
            if not steps:
                break

            # Executa passos pendentes
            executed_steps = self.executor.execute(steps, max_iterations=8)

            # Se todos falharam ou objetivo trivial
            done_steps = [s for s in executed_steps if s.get("status") == "done"]
            if not done_steps and cycle == 1:
                break

            # Reflete se precisa continuar
            reflection = self.reflector.evaluate(
                user_message=user_message,
                objective=plan.get("objective", "Responder Juan"),
                executed_steps=executed_steps,
                context_text=context_text,
            )

            if not reflection.get("needs_more_steps"):
                break

            additional = reflection.get("additional_steps", []) or []
            if not additional:
                break

            # Converte additional_steps no formato do planner
            next_steps: list[dict[str, Any]] = []
            base = len(executed_steps)
            for i, step in enumerate(additional):
                tool_name = step.get("tool", "direct_response")
                if not get_tool(tool_name):
                    # Reflector também inventou tool; converte para search_alerts/query_db
                    args = step.get("args", {})
                    query_parts = [str(v) for v in args.values() if v is not None]
                    query = " ".join(query_parts)[:200] or f"busca adicional {i+1}"
                    tool_name = "search_alerts"
                    args = {"query": query, "limit": 10}
                next_steps.append({
                    "step_number": base + i + 1,
                    "tool": tool_name,
                    "args": step.get("args", {}),
                    "reason": step.get("reason", "Passo adicional do ReflectorAgent."),
                    "depends_on": [],
                    "status": "pending",
                    "result": None,
                })

            # Atualiza plano para novo ciclo
            plan["steps"] = self._filter_valid_steps(executed_steps + next_steps)
            plan["objective"] = plan.get("objective", "Responder Juan")

        # 5. Se algum step exigiu aprovacao, resposta eh o card de aprovacao
        approval_steps = [
            s for s in executed_steps
            if s.get("result", {}).get("approval_required")
        ]
        if approval_steps:
            first_approval = approval_steps[0]["result"]
            response = first_approval.get("message", "⏸️ Ação aguardando sua aprovação.")
            self.ingestor.ingest_conversation(user_message, response, topic="approval_request")
            self._update_history(user_message, response, "resolve_approval")
            return {
                "success": True,
                "response": response,
                "tool_used": "resolve_approval",
                "objective": plan.get("objective", "Responder Juan"),
                "steps": executed_steps,
                "cycles": cycle,
                "approval_required": True,
                "approval_id": first_approval.get("approval_id"),
            }

        # 6. Sintetiza resposta normal
        response = self.synthesizer.synthesize(
            user_message=user_message,
            objective=plan.get("objective", "Responder Juan"),
            executed_steps=executed_steps,
            context_text=context_text,
        )

        # Identifica tool principal usada
        tool_used = self._main_tool_from_steps(executed_steps) or "direct_response"

        # 6. Persiste
        self.ingestor.ingest_conversation(user_message, response, topic=tool_used)
        self._update_history(user_message, response, tool_used)

        return {
            "success": True,
            "response": response,
            "tool_used": tool_used,
            "objective": plan.get("objective", "Responder Juan"),
            "steps": executed_steps,
            "cycles": cycle,
            "needs_confirmation": bool(plan.get("needs_confirmation", False)),
            "confirmation_message": plan.get("confirmation_message", ""),
        }

    # ── Tool runner interno ────────────────────────────────────────────

    def _execute_tool_internal(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """ExecutorAgent chama este método para rodar cada tool."""
        tool = get_tool(tool_name)
        if not tool:
            return {"success": False, "error": f"Ferramenta '{tool_name}' desconhecida."}

        try:
            if tool_name == "direct_response":
                return {"success": True, "text": args.get("text", "")}

            if tool_name == "query_db":
                from hermes.secretary.tools.tool_query_db import run_query
                return run_query(args.get("query", ""))

            if tool_name == "search_alerts":
                from hermes.secretary.tools.tool_search_alerts import search_alerts
                return search_alerts(
                    query=args.get("query", ""),
                    alert_type=args.get("alert_type"),
                    status=args.get("status"),
                    days=args.get("days"),
                    limit=args.get("limit", 10),
                )

            if tool_name == "run_skill":
                from hermes.secretary.tools.tool_run_skill import run_skill
                skill_name = args.get("skill_name", "")
                payload = args.get("payload", {})
                timeout = 300 if skill_name == "video" else None
                result = run_skill(skill_name, payload, timeout=timeout)

                if skill_name == "lead_finder" and not payload.get("auto_save", False):
                    self.last_lead_finder_result = {
                        "city": payload.get("city", ""),
                        "state": payload.get("state", ""),
                        "segment": payload.get("segment", ""),
                        "max_results": payload.get("max_results", 10),
                    }
                elif skill_name == "lead_finder" and payload.get("auto_save", False):
                    self.last_lead_finder_result = None

                if skill_name == "video":
                    self.last_video_result = {
                        "video_path": payload.get("video_path", ""),
                        "caption": payload.get("caption", ""),
                        "context_type": result.get("context_type"),
                        "minute": result.get("minute"),
                        "transcript": result.get("transcript"),
                        "raw": result.get("raw"),
                    }

                return result

            if tool_name == "add_lead":
                from hermes.secretary.tools.tool_add_lead import add_lead
                return add_lead(
                    name=args.get("name", ""),
                    phone=args.get("phone", ""),
                    company=args.get("company"),
                    email=args.get("email"),
                    segment=args.get("segment", "outro"),
                    notes=args.get("notes"),
                    city=args.get("city"),
                    temperature=args.get("temperature", "warm"),
                )

            if tool_name == "enrich_lead":
                from hermes.secretary.tools.tool_enrich_lead import enrich_lead
                return enrich_lead(
                    lead_id=args.get("lead_id"),
                    lead_name=args.get("lead_name"),
                )

            if tool_name == "whatsapp_analyzer":
                from hermes.secretary.tools.tool_whatsapp_analyzer import run as wa_run
                return wa_run(
                    lead_id=args.get("lead_id"),
                    phone=args.get("phone"),
                )

            if tool_name == "cancel_meeting":
                from hermes.secretary.tools.tool_cancel_meeting import cancel_meeting
                return cancel_meeting(
                    meeting_id=args.get("meeting_id"),
                    title_fragment=args.get("title_fragment"),
                    reason=args.get("reason", "Cancelado via Hermes Secretary"),
                )

            if tool_name == "update_memory":
                from hermes.secretary.tools.tool_update_memory import update_memory
                return update_memory(
                    action=args.get("action", "add"),
                    category=args.get("category", "geral"),
                    key=args.get("key", ""),
                    value=args.get("value", ""),
                    confidence=args.get("confidence", 0.9),
                )

            if tool_name == "create_alert":
                from hermes.secretary.tools.tool_create_alert import create_alert
                return create_alert(
                    alert_type=args.get("alert_type", "info"),
                    title=args.get("title", ""),
                    description=args.get("description", ""),
                    lead_id=args.get("lead_id"),
                    company_id=args.get("company_id"),
                    suggested_action=args.get("suggested_action"),
                )

            if tool_name == "manage_tasks":
                from hermes.secretary.tools.tool_manage_tasks import execute_manage_tasks
                return execute_manage_tasks(
                    action=args.get("action", ""),
                    title=args.get("title", ""),
                    due_date=args.get("due_date", ""),
                    task_id=args.get("task_id", ""),
                )

            if tool_name == "draft_email":
                from hermes.secretary.tools.tool_draft_email import execute_draft_email
                return execute_draft_email(
                    recipient=args.get("recipient", ""),
                    subject=args.get("subject", ""),
                    body=args.get("body", ""),
                )

            if tool_name == "web_search":
                from hermes.secretary.tools.tool_web_search import execute_web_search
                return execute_web_search(args.get("action", ""), args.get("query", ""))

            if tool_name == "portfolio_builder":
                from hermes.secretary.tools.tool_portfolio_builder import run as pb_run
                return pb_run(
                    tesis_type=args.get("tesis_type"),
                    valor=args.get("valor"),
                    acao=args.get("acao"),
                    horizonte=args.get("horizonte"),
                )

            if tool_name == "project_manager":
                from hermes.secretary.tools.tool_project_manager import run as pm_run
                return pm_run(
                    action=args.get("action", ""),
                    name=args.get("name", ""),
                    project_id=args.get("project_id"),
                    status=args.get("status"),
                    priority=args.get("priority"),
                    notes=args.get("notes"),
                )

            if tool_name == "agent_council":
                from hermes.secretary.tools.tool_agent_council import run as ac_run
                return ac_run(question=args.get("question", ""))

            if tool_name == "generate_code":
                from hermes.secretary.tools.tool_generate_code import execute_generate_code
                return execute_generate_code(
                    description=args.get("description", ""),
                    language=args.get("language", "python"),
                    save_path=args.get("save_path"),
                )

            if tool_name == "run_code":
                from hermes.secretary.tools.tool_run_code import execute_run_code
                return execute_run_code(
                    file_path=args.get("file_path"),
                    code=args.get("code"),
                    confirmed=args.get("confirmed", False),
                )

            if tool_name == "save_video_summary":
                from hermes.secretary.tools.tool_save_video_summary import save_video_summary
                result = save_video_summary(
                    context_type=args.get("context_type", "outro"),
                    minute=args.get("minute", {}),
                    transcript=args.get("transcript"),
                    video_path=args.get("video_path", ""),
                    caption=args.get("caption", ""),
                    lead_id=args.get("lead_id"),
                    company_id=args.get("company_id"),
                    meeting_id=args.get("meeting_id"),
                )
                if result.get("success"):
                    self.last_video_result = None
                return result

            if tool_name == "manage_workflows":
                from hermes.secretary.tools.tool_manage_workflows import execute_manage_workflows
                return execute_manage_workflows(
                    action=args.get("action", ""),
                    name=args.get("name"),
                    workflow_id=args.get("workflow_id"),
                    definition_json=args.get("definition_json"),
                    description=args.get("description"),
                    category=args.get("category"),
                )

            if tool_name == "run_workflow":
                from hermes.secretary.tools.tool_run_workflow import execute_run_workflow
                return execute_run_workflow(
                    action=args.get("action", ""),
                    workflow_id=args.get("workflow_id"),
                    context_json=args.get("context_json", "{}"),
                )

            if tool_name == "list_approvals":
                from hermes.secretary.tools.tool_list_approvals import list_approvals
                return list_approvals(
                    status=args.get("status", "pending"),
                    limit=args.get("limit", 20),
                )

            if tool_name == "resolve_approval":
                from hermes.secretary.tools.tool_resolve_approval import resolve_approval
                return resolve_approval(
                    approval_id=args.get("approval_id"),
                    resolution=args.get("resolution", "approved"),
                    execute=args.get("execute", True),
                )

            return {"success": False, "error": f"Tool '{tool_name}' registrada mas não implementada."}

        except Exception as exc:
            traceback.print_exc()
            return {"success": False, "error": str(exc)}

    def _execute_tool_gated(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Wrapper que aplica o gate de aprovacao antes de executar uma tool.

        Se a tool for autonoma, executa diretamente via _execute_tool_internal.
        Se precisar de aprovacao, cria um pending_approval e retorna mensagem
        pedindo confirmacao, sem executar.
        """
        ctx = context or {
            "user_message": self._last_user_message,
            "source": self._last_source,
        }
        gate = self.approval_agent.evaluate(tool_name, args, ctx)

        if gate.get("autonomous"):
            return self._execute_tool_internal(tool_name, args)

        # Cria aprovacao pendente
        try:
            approval = self.approval_repo.create(
                approval_type=tool_name,
                title=gate.get("title", f"Ação '{tool_name}' precisa de aprovação"),
                description=gate.get("description", ""),
                draft_payload=gate.get("draft_payload", {"tool": tool_name, "args": args}),
                source=ctx.get("source", "hermes_core"),
                requested_by="hermes",
                metadata={
                    "user_message": ctx.get("user_message", ""),
                    "reason": gate.get("reason", ""),
                },
            )

            return {
                "success": True,
                "approval_required": True,
                "approval_id": approval.id,
                "type": tool_name,
                "title": approval.title,
                "description": approval.description,
                "message": (
                    f"⏸️ Ação '{tool_name}' precisa da sua aprovação.\n\n"
                    f"*{approval.title}*\n"
                    f"{approval.description}\n\n"
                    f"Para aprovar, diga: *aprovar {approval.id}*\n"
                    f"Para rejeitar, diga: *rejeitar {approval.id}*"
                ),
            }
        except Exception as exc:
            traceback.print_exc()
            return {
                "success": False,
                "approval_required": True,
                "error": f"Não foi possível criar aprovação: {exc}",
                "message": f"⏸️ Ação '{tool_name}' precisaria de aprovação, mas ocorreu um erro ao registrar.",
            }

    # ── Keyword Detection (atalhos rápidos) ─────────────────────────

    def _detect_intent(self, text: str) -> dict[str, Any] | None:
        t = text.lower().strip()

        # Confirmar salvamento do último preview do lead_finder
        if self.last_lead_finder_result and any(p in t for p in [
            "salvar", "salva", "salve", "confirmar", "confirmo", "guardar", "guarda",
            "sim", "pode salvar", "salva esses", "salvar esses", "salvar no crm",
        ]):
            preview = self.last_lead_finder_result
            return {
                "tool": "run_skill",
                "args": {
                    "skill_name": "lead_finder",
                    "payload": {
                        "city": preview.get("city", ""),
                        "state": preview.get("state", ""),
                        "segment": preview.get("segment", ""),
                        "max_results": preview.get("max_results", 10),
                        "auto_save": True,
                    },
                },
            }

        # Salvar minuta do último vídeo processado
        if self.last_video_result and any(p in t for p in [
            "salvar minuta", "salvar resumo", "salvar vídeo", "salvar video",
            "guardar minuta", "guardar resumo", "salva minuta", "salva resumo",
            "persistir minuta", "salvar no crm", "crm minuta", "salvar reunião",
        ]):
            video_result = self.last_video_result
            chosen_context = None
            for ctx in ["lead", "escritorio", "marketing", "outro"]:
                if ctx in t:
                    chosen_context = ctx
                    break
            effective_context = chosen_context or video_result.get("context_type", "outro")
            minute = video_result.get("minute")
            if not minute:
                return {
                    "tool": "run_skill",
                    "args": {
                        "skill_name": "video",
                        "payload": {
                            "video_path": video_result.get("video_path", ""),
                            "action": "summarize",
                            "caption": video_result.get("caption", ""),
                            "context_hint": effective_context,
                        },
                    },
                }
            return {
                "tool": "save_video_summary",
                "args": {
                    "context_type": effective_context,
                    "minute": minute,
                    "transcript": video_result.get("transcript"),
                    "video_path": video_result.get("video_path", ""),
                    "caption": video_result.get("caption", ""),
                },
            }

        # Comandos diretos de contagem
        if any(p in t for p in ["quantos leads", "quantas leads", "total de leads"]):
            return {"tool": "query_db", "args": {"query": "SELECT COUNT(*) as total FROM leads"}}
        if any(p in t for p in ["hot leads", "lead hot", "leads hot", "quantos hot"]):
            return {"tool": "query_db", "args": {"query": "SELECT COUNT(*) as total FROM leads WHERE hot_lead = 1"}}
        if any(p in t for p in ["oportunidades abertas", "quantas opp", "quantas oportunidades"]):
            return {"tool": "query_db", "args": {"query": "SELECT COUNT(*) as total FROM opportunities WHERE status = 'aberta'"}}
        if any(p in t for p in ["reuniões hoje", "reunioes hoje", "quantas reunioes", "quantas reuniões"]):
            return {"tool": "query_db", "args": {"query": "SELECT COUNT(*) as total FROM meetings WHERE DATE(scheduled_start) = DATE('now')"}}
        if any(p in t for p in ["alertas pendentes", "alertas novos", "quantos alertas"]):
            return {"tool": "query_db", "args": {"query": "SELECT COUNT(*) as total FROM hermes_alerts WHERE status = 'novo'"}}

        # Comandos de listagem
        if any(p in t for p in ["lista leads", "mostra leads", "ver leads", "quais leads", "meus leads"]):
            return {"tool": "query_db", "args": {"query": "SELECT id, company_name, score_total, temperature, conversation_status FROM leads ORDER BY created_at DESC LIMIT 10"}}
        if any(p in t for p in ["lista reuniões", "mostra reuniões", "ver reuniões", "próximas reuniões"]):
            return {"tool": "query_db", "args": {"query": "SELECT id, title, scheduled_start, meeting_status FROM meetings WHERE scheduled_start >= DATE('now') ORDER BY scheduled_start ASC LIMIT 10"}}
        if any(p in t for p in ["lista oportunidades", "mostra oportunidades", "ver oportunidades", "pipeline"]):
            return {"tool": "query_db", "args": {"query": "SELECT id, title, stage, estimated_value, status FROM opportunities WHERE status = 'aberta' ORDER BY updated_at DESC LIMIT 10"}}

        # Skills diretas
        if any(p in t for p in ["digest", "resumo do dia", "digest de hoje", "como foi o dia"]):
            return {"tool": "run_skill", "args": {"skill_name": "daily_digest", "payload": {}}}
        if any(p in t for p in ["alertas do dia", "check up", "check-up", "monitoramento", "alertas proativos", "status da operação", "como tá a operação"]):
            return {"tool": "run_skill", "args": {"skill_name": "market_monitor", "payload": {}}}

        # Memória pessoal
        if any(p in t for p in ["lembra que", "lembrar que", "anota que", "guarda que", "salva que", "anota isso"]):
            for prefix in ["lembra que", "lembrar que", "anota que", "guarda que", "salva que", "anota isso"]:
                if prefix in t:
                    fact = t.split(prefix, 1)[1].strip()
                    return {"tool": "update_memory", "args": {"action": "add", "category": "preferencia", "key": fact[:50], "value": fact}}

        # Snapshot
        if any(p in t for p in ["snapshot", "status do sistema", "como tá o sistema", "como ta o sistema", "resumo do sistema"]):
            return {"tool": "direct_response", "args": {"text": self._get_system_snapshot_text()}}

        # Capacidades
        if any(p in t for p in [
            "o que você sabe", "o que tu sabe", "quais skills", "quais ferramentas",
            "lista skills", "lista ferramentas", "o que faz", "capacidades", "me ajuda",
            "o que tu faz", "o que voce faz", "o que vc faz", "comandos",
        ]):
            return {"tool": "direct_response", "args": {"text": self._list_capabilities()}}

        # Aprovar/rejeitar aprovacao pendente
        approve_match = re.search(r"(?:aprovar?|aprova|ok|pode fazer|pode executar)\s+(?:a?prova[cç][aã]o\s*)?(\d+)", t)
        reject_match = re.search(r"(?:rejeitar?|rejeita|n[aã]o|cancelar)\s+(?:a?prova[cç][aã]o\s*)?(\d+)", t)
        if approve_match:
            return {"tool": "resolve_approval", "args": {"approval_id": int(approve_match.group(1)), "resolution": "approved"}}
        if reject_match:
            return {"tool": "resolve_approval", "args": {"approval_id": int(reject_match.group(1)), "resolution": "rejected"}}

        # Listar aprovacoes pendentes
        if any(p in t for p in [
            "aprovações pendentes", "aprovações", "aprovacoes pendentes", "aprovacoes",
            "o que precisa de aprovação", "o que precisa de aprovacao",
            "o que está esperando", "o que esta esperando", "pendencias de aprovacao",
        ]):
            return {"tool": "list_approvals", "args": {"status": "pending"}}

        return None

    # ── Helpers ──────────────────────────────────────────────────────

    def _filter_valid_steps(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        valid = []
        for step in steps:
            tool_name = step.get("tool", "")
            args = step.get("args", {})
            if get_tool(tool_name):
                valid.append(step)
            else:
                # Tenta mapear ferramentas de busca inválidas para search_alerts/query_db
                query_parts = []
                for v in args.values():
                    if isinstance(v, str):
                        query_parts.append(v)
                    elif isinstance(v, list) and v and isinstance(v[0], str):
                        query_parts.extend(v)
                query = " ".join(query_parts)[:200] or f"busca sobre {tool_name}"
                valid.append({
                    "step_number": step.get("step_number", len(valid) + 1),
                    "tool": "search_alerts",
                    "args": {"query": query, "limit": 10},
                    "reason": f"Ferramenta '{tool_name}' não existe; substituída por busca em alertas.",
                    "depends_on": [],
                    "status": "pending",
                    "result": None,
                })
        return valid

    def _detect_search_intent(self, text: str) -> str | None:
        """Detecta se a mensagem é claramente uma busca e retorna termos."""
        t = text.lower().strip()
        search_triggers = [
            "onde está", "onde esta", "onde fica", "me acha", "me mostra", "mostra",
            "busca", "procura", "encontra", "resumo de", "resumo da", "notas sobre",
            "alertas sobre", "briefing de", "minuta de", "reunião de", "reuniao de",
        ]
        if not any(trigger in t for trigger in search_triggers):
            return None
        cleaned = t
        for trigger in search_triggers:
            cleaned = cleaned.replace(trigger, "")
        for w in ["o", "a", "os", "as", "um", "uma", "meu", "minha", "me", "eu", "você", "hermes", "?"]:
            cleaned = cleaned.replace(w, "")
        cleaned = cleaned.strip()
        return cleaned if len(cleaned) >= 2 else None

    def _main_tool_from_steps(self, steps: list[dict[str, Any]]) -> str | None:
        for step in steps:
            tool = step.get("tool", "")
            result = step.get("result", {})
            if tool and tool != "direct_response" and result and result.get("success"):
                return tool
        return None

    def _update_history(self, user_message: str, final_response: str, tool_name: str) -> None:
        self.history.append({
            "user": user_message,
            "assistant_raw": final_response,
            "tool": tool_name,
            "timestamp": datetime.now(UTC).isoformat(),
        })
        if len(self.history) > self.max_history * 2:
            self.history = self.history[-self.max_history:]

    def _get_system_snapshot_text(self) -> str:
        try:
            from app.database import get_connection
            with get_connection() as conn:
                total_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
                hot_leads = conn.execute("SELECT COUNT(*) FROM leads WHERE hot_lead = 1").fetchone()[0]
                open_opps = conn.execute("SELECT COUNT(*) FROM opportunities WHERE status = 'aberta'").fetchone()[0]
                today_meetings = conn.execute("SELECT COUNT(*) FROM meetings WHERE DATE(scheduled_start) = DATE('now')").fetchone()[0]
                pending_alerts = conn.execute("SELECT COUNT(*) FROM hermes_alerts WHERE status = 'novo'").fetchone()[0]

            return (
                f"📊 Snapshot do sistema ({datetime.now(UTC).strftime('%H:%M')}):\n"
                f"  • Leads totais: {total_leads} | Hot: {hot_leads}\n"
                f"  • Oportunidades abertas: {open_opps}\n"
                f"  • Reuniões hoje: {today_meetings}\n"
                f"  • Alertas pendentes: {pending_alerts}"
            )
        except Exception:
            return ""

    def _list_capabilities(self) -> str:
        return (
            "🤖 **Hermes Secretary — Seu Copiloto de Assessoria**\n\n"
            "📊 **Negócios:** leads, oportunidades, pipeline, hot leads, reuniões, alertas\n"
            "📅 **Agenda:** cancelar reuniões, ver agenda, snapshot comercial\n"
            "📱 **Conversas:** analisar conversas de cliente, sentimento, follow-up\n"
            "💼 **Carteira:** teses, análise de concentração, rebalanceamento\n"
            "📋 **Projetos:** criar e listar projetos pessoais\n"
            "🧠 **Memória:** lembrar preferências, rotinas, contatos, contexto de clientes\n"
            "🎬 **Vídeo:** transcrever, resumir, sugerir cortes, renderizar reels\n"
            "🔐 **Aprovações:** ações externas/destrutivas precisam do seu OK\n"
            "🚨 **Monitoramento:** alertas proativos da operação comercial\n\n"
            "Quer testar alguma? Só mandar!"
        )

    # ── API pública de aprovações ──────────────────────────────────────

    def execute_or_request_approval(
        self,
        tool_name: str,
        args: dict[str, Any],
        source: str = "api",
    ) -> dict[str, Any]:
        """Executa uma tool autonomamente ou cria pending_approval.

        Use por interfaces externas (cockpit, bot, API) que querem rodar uma
        ação específica sem passar pelo chat completo.
        """
        self._last_user_message = f"execute {tool_name}"
        self._last_source = source
        return self._execute_tool_gated(tool_name, args)

    def list_pending_approvals(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retorna lista de aprovações pendentes serializadas."""
        return [a.to_dict() for a in self.approval_repo.list_pending(limit=limit)]

    def resolve_approval(
        self,
        approval_id: int,
        resolution: str,
        execute: bool = True,
    ) -> dict[str, Any]:
        """Aprova/rejeita um pending_approval e executa se aprovado."""
        from hermes.secretary.tools.tool_resolve_approval import resolve_approval
        return resolve_approval(approval_id, resolution, execute=execute)
