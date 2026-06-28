"""Hermes Secretary Core — Motor de Intenções e Orquestrador.

NOVA ARQUITETURA (v2.0 — Livre e Evolutivo):
1. O LLM responde naturalmente, sem restrições de JSON.
2. O core detecta intenções por palavras-chave (fallback inteligente).
3. Se detectar comando conhecido → executa tool → pede ao LLM para formular resposta natural.
4. Se NÃO detectar → deixa o LLM responder livremente.
5. O LLM aprende com o contexto e evolui com o Juan.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

# Ensure project root is on path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_env
from app.llm_client import UnifiedLLMClient
from hermes.secretary.context.personal_memory import PersonalMemory
from hermes.memory.ingestor import Ingestor
from hermes.memory.retriever import Retriever
from hermes.memory.background_runner import BackgroundRunner

load_env()


class HermesCore:
    """Orquestrador central do Secretário Hermes — Modo Livre."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_history: int = 10,
    ):
        self.llm = UnifiedLLMClient(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=180,
        )
        self.memory = PersonalMemory()          # JSON legado (compat)
        self.background = BackgroundRunner()    # Novo: inference + dedup + cleanup + enrichment
        self.ingestor = Ingestor(background=self.background)  # Novo: grafo + vetor
        self.retriever = Retriever()            # Novo: busca semântica
        self.max_history = max_history
        self.history: list[dict[str, Any]] = []
        self.last_lead_finder_result: dict[str, Any] | None = None
        self.last_video_result: dict[str, Any] | None = None

        # Inicia background tasks (inferência, dedup, cleanup, enrichment)
        self.background.start()

    # ── Public API ───────────────────────────────────────────────────

    def process_message(self, user_message: str) -> dict[str, Any]:
        """Processa uma mensagem do usuário."""
        try:
            return self._process(user_message)
        except Exception as exc:
            traceback.print_exc()
            return {
                "success": False,
                "error": str(exc),
                "response": "❌ Algo deu errado internamente. Tente novamente.",
            }

    def _process(self, user_message: str) -> dict[str, Any]:
        # 1. DETECTA INTENÇÃO POR KEYWORDS (rápido, confiável)
        intent = self._detect_intent(user_message)

        # 2. SE KEYWORDS NÃO BASTA, PERGUNTA AO LLM ROUTER
        if not intent:
            intent = self._route_with_llm(user_message)

        if intent:
            # É um comando conhecido → executa tool
            tool_result = self._execute_tool(intent["tool"], intent.get("args", {}))

            if not tool_result.get("success"):
                error_msg = tool_result.get("error", "Erro desconhecido")
                response = f"⚠️ Não consegui fazer isso: {error_msg}"
            else:
                # Pede ao LLM para formular resposta natural sobre o resultado
                response = self._formulate_natural_response(
                    user_message=user_message,
                    tool_name=intent["tool"],
                    tool_result=tool_result,
                )

            # Salva conversa na memória convexa (grafo + vetor)
            self.ingestor.ingest_conversation(user_message, response, topic=intent["tool"])
            self._update_history(user_message, response, intent["tool"])
            return {
                "success": True,
                "response": response,
                "tool_used": intent["tool"],
            }

        # 3. NÃO É COMANDO CONHECIDO → LLM responde livremente
        response = self._chat_freely(user_message)
        self.ingestor.ingest_conversation(user_message, response, topic="chat_livre")
        self._update_history(user_message, response, "direct_response")
        return {
            "success": True,
            "response": response,
            "tool_used": "direct_response",
        }

    # ── Detection Engine (Fallback Inteligente) ──────────────────────

    def _detect_intent(self, text: str) -> dict[str, Any] | None:
        """Detecta intenção por palavras-chave. Rápido, confiável, NÃO depende do LLM."""
        t = text.lower().strip()

        # ── Confirmar salvamento do último preview do lead_finder ─────
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

        # ── Salvar minuta do último vídeo processado ────────────────
        if self.last_video_result and any(p in t for p in [
            "salvar minuta", "salvar resumo", "salvar vídeo", "salvar video",
            "guardar minuta", "guardar resumo", "salva minuta", "salva resumo",
            "persistir minuta", "salvar no crm", "crm minuta", "salvar reunião",
        ]):
            video_result = self.last_video_result
            # Se o usuário escolheu contexto na mensagem (lead/escritorio/marketing/outro)
            chosen_context = None
            for ctx in ["lead", "escritorio", "marketing", "outro"]:
                if ctx in t:
                    chosen_context = ctx
                    break
            effective_context = chosen_context or video_result.get("context_type", "outro")

            # Se ainda não temos minuta (só transcrição/frames), reprocessa com summarize
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

        # ── Capacidades / Ajuda ──────────────────────────────────────
        if any(p in t for p in [
            "o que você sabe", "o que tu sabe", "quais skills", "quais ferramentas",
            "lista skills", "lista ferramentas", "o que faz", "capacidades", "me ajuda",
            "o que tu faz", "o que voce faz", "o que vc faz", "comandos",
        ]):
            return {
                "tool": "direct_response",
                "args": {"text": self._list_capabilities()},
            }

        # ── Contagens ──────────────────────────────────────────────────
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

        # ── Busca alertas / conhecimento / notas ───────────────────────
        if any(p in t for p in [
            "notas internas", "conhecimento do escritorio", "conhecimento do escritório",
            "resumos salvos", "minutas salvas", "resumo da reuniao", "resumo da reunião",
            "onde esta o resumo", "onde está o resumo", "meus alertas", "ver alertas",
            "briefings de marketing", "briefing de marketing",
        ]):
            return {"tool": "search_alerts", "args": {"query": text, "limit": 10}}

        # fallback: mensagens curtas que parecem busca por resumo/alerts
        if any(p in t for p in ["resumo", "minuta", "alerta", "nota", "briefing"]) and any(p in t for p in ["onde", "achar", "encontrar", "mostra", "ver", "último", "ultimo", "meus"]):
            return {"tool": "search_alerts", "args": {"query": text, "limit": 10}}

        # ── Listagens ──────────────────────────────────────────────────
        if any(p in t for p in ["lista leads", "mostra leads", "ver leads", "quais leads", "meus leads"]):
            return {"tool": "query_db", "args": {"query": "SELECT id, company_name, score_total, temperature, conversation_status FROM leads ORDER BY created_at DESC LIMIT 10"}}
        if any(p in t for p in ["lista reuniões", "mostra reuniões", "ver reuniões", "próximas reuniões"]):
            return {"tool": "query_db", "args": {"query": "SELECT id, title, scheduled_start, meeting_status FROM meetings WHERE scheduled_start >= DATE('now') ORDER BY scheduled_start ASC LIMIT 10"}}
        if any(p in t for p in ["lista oportunidades", "mostra oportunidades", "ver oportunidades", "pipeline"]):
            return {"tool": "query_db", "args": {"query": "SELECT id, title, stage, estimated_value, status FROM opportunities WHERE status = 'aberta' ORDER BY updated_at DESC LIMIT 10"}}

        # ── Skills ─────────────────────────────────────────────────────
        if any(p in t for p in ["digest", "resumo do dia", "digest de hoje", "como foi o dia"]):
            return {"tool": "run_skill", "args": {"skill_name": "daily_digest", "payload": {}}}
        if any(p in t for p in ["briefing", "resumo da empresa", "gera briefing"]):
            return {"tool": "run_skill", "args": {"skill_name": "generate_briefing", "payload": {}}}
        if any(p in t for p in ["alertas do dia", "check up", "check-up", "monitoramento", "alertas proativos", "status da operação", "como tá a operação"]):
            return {"tool": "run_skill", "args": {"skill_name": "market_monitor", "payload": {}}}
        if any(p in t for p in ["analisar conversa", "analisar whatsapp", "sentimento do lead", "analise do lead", "analisa conversa", "whatsapp do lead"]):
            return {"tool": "run_skill", "args": {"skill_name": "whatsapp_analyzer", "payload": {}}}

        # ── Enriquecer lead / score ─────────────────────────────────────
        if any(p in t for p in [
            "enriquece", "enriquecer", "score", "score ia", "scorear", "faz o enriquecimento",
            "enriquecimento", "score para", "pontua", "qualifica",
        ]):
            # Tenta extrair lead_id numérico
            import re
            id_match = re.search(r'\b(\d{1,6})\b', text)
            if id_match:
                return {"tool": "enrich_lead", "args": {"lead_id": int(id_match.group(1))}}

            # Tenta achar lead pelo nome na mensagem
            name_fragment = text
            for trigger in [
                "enriquece", "enriquecer", "score", "score ia", "scorear", "faz o enriquecimento",
                "enriquecimento", "score para", "pontua", "qualifica", "o lead", "a lead", "do lead", "da lead",
            ]:
                name_fragment = name_fragment.lower().replace(trigger, "")
            name_fragment = name_fragment.strip(" ,.;:-")
            if name_fragment and len(name_fragment) >= 3:
                return {"tool": "enrich_lead", "args": {"lead_name": name_fragment}}
            return {"tool": "enrich_lead", "args": {}}

        # ── Portfolio / Investimentos ──────────────────────────────────
        if any(p in t for p in ["tese 70/30", "tese de investimento", "montar carteira", "alocar", "alocação", "portfolio", "carteira 70 30"]):
            # Tenta extrair valor
            import re
            valor_match = re.search(r'(\d+[\d.,]*)', t)
            valor = float(valor_match.group(1).replace('.', '').replace(',', '.')) if valor_match else 100000
            return {"tool": "run_skill", "args": {"skill_name": "portfolio_builder", "payload": {"tesis_type": "70_30", "valor": valor}}}
        if any(p in t for p in ["analise da ação", "analisa ação", "analise de ação", "analise de"]):
            # Tenta extrair ticker
            words = text.upper().split()
            for w in words:
                if len(w) >= 4 and w.isalpha() and w not in ["QUAL", "ACAO", "ACAO", "AÇÃO", "TICKER", "ANALISE", "ANALISA"]:
                    return {"tool": "run_skill", "args": {"skill_name": "portfolio_builder", "payload": {"acao": w}}}

        # ── Projetos ───────────────────────────────────────────────────
        if any(p in t for p in ["lista projetos", "ver projetos", "meus projetos", "projetos pessoais"]):
            return {"tool": "run_skill", "args": {"skill_name": "project_manager", "payload": {"action": "list"}}}
        if any(p in t for p in ["criar projeto", "novo projeto", "adicionar projeto"]):
            name = text.lower().replace("criar projeto", "").replace("novo projeto", "").replace("adicionar projeto", "").strip()
            return {"tool": "run_skill", "args": {"skill_name": "project_manager", "payload": {"action": "create", "name": name or "Novo projeto"}}}

        # ── Cancelar reunião ──────────────────────────────────────────
        if any(p in t for p in ["cancela reunião", "cancela minha reunião", "cancelar reunião", "cancela a reunião"]):
            fragment = text.lower().replace("cancela", "").replace("minha", "").replace("reunião", "").replace("reuniao", "").replace("a", "").strip()
            return {"tool": "cancel_meeting", "args": {"title_fragment": fragment or None, "reason": "Cancelado via Hermes Secretary"}}

        # ── Memória pessoal ──────────────────────────────────────────
        if any(p in t for p in ["lembra que", "lembrar que", "anota que", "guarda que", "salva que", "anota isso"]):
            for prefix in ["lembra que", "lembrar que", "anota que", "guarda que", "salva que", "anota isso"]:
                if prefix in t:
                    fact = t.split(prefix, 1)[1].strip()
                    return {"tool": "update_memory", "args": {"action": "add", "category": "preferencia", "key": fact[:50], "value": fact}}

        # ── Snapshot ─────────────────────────────────────────────────────
        if any(p in t for p in ["snapshot", "status do sistema", "como tá o sistema", "como ta o sistema", "resumo do sistema"]):
            return {"tool": "direct_response", "args": {"text": self._get_system_snapshot_text()}}

        # ── Web Search ─────────────────────────────────────────────────
        if any(p in t for p in [
            "pesquisa na web", "busca na internet", "google sobre", "noticias sobre",
            "pesquisar sobre", "procura sobre", "search", "duckduckgo",
        ]):
            query = text
            for trigger in ["pesquisa na web", "busca na internet", "google sobre", "noticias sobre",
                            "pesquisar sobre", "procura sobre", "search", "duckduckgo"]:
                query = query.lower().replace(trigger, "")
            return {"tool": "web_search", "args": {"action": "search", "query": query.strip() or text}}
        if any(p in t for p in ["le essa url", "ler url", "resumir site", "resumir link", "read url"]):
            url = text.split()[-1] if "http" in text else ""
            return {"tool": "web_search", "args": {"action": "read_url", "query": url or text}}

        # ── Task Manager ───────────────────────────────────────────────
        # ── Cadastrar lead ─────────────────────────────────────────────
        if any(p in t for p in [
            "cadastra lead", "novo lead", "adiciona lead", "criar lead",
            "registrar lead", "salvar lead", "lead novo", "cadastrar lead",
        ]):
            import re
            # Tenta extrair telefone (com DDD, 10 ou 11 dígitos)
            phone_match = re.search(r'(\(?\d{2}\)?\s*\d{4,5}[-.\s]?\d{4})', text)
            phone = phone_match.group(1) if phone_match else ""
            # Remove telefone e triggers do texto para tentar achar nome/empresa
            remaining = text
            for trigger in ["cadastra lead", "novo lead", "adiciona lead", "criar lead",
                            "registrar lead", "salvar lead", "lead novo", "cadastrar lead"]:
                remaining = remaining.lower().replace(trigger, "")
            if phone:
                remaining = remaining.replace(phone_match.group(1), "")
            remaining = remaining.strip()

            # Heurística simples: primeiro token maiúsculo ou sequência de palavras antes de vírgula/traço
            name = remaining.split(",")[0].split("-")[0].strip() if remaining else "Novo lead"
            if not name or len(name) < 2:
                name = "Novo lead"

            return {"tool": "add_lead", "args": {
                "name": name,
                "phone": phone or "",
                "notes": text,
            }}

        # ── Tarefas ────────────────────────────────────────────────────
        if any(p in t for p in [
            "adiciona tarefa", "nova tarefa", "criar tarefa", "anotar tarefa",
            "lembra de fazer", "task nova", "todo",
        ]):
            task = text
            for trigger in ["adiciona tarefa", "nova tarefa", "criar tarefa", "anotar tarefa",
                              "lembra de fazer", "task nova", "todo"]:
                task = task.lower().replace(trigger, "")
            return {"tool": "manage_tasks", "args": {"action": "add", "title": task.strip() or "Nova tarefa"}}
        if any(p in t for p in ["lista tarefas", "ver tarefas", "tarefas pendentes", "minhas tarefas"]):
            return {"tool": "manage_tasks", "args": {"action": "list"}}
        if any(p in t for p in ["conclui tarefa", "completar tarefa", "fechar tarefa", "done task"]):
            # tenta extrair ID
            words = text.split()
            task_id = ""
            for w in words:
                if w.isalnum() and len(w) >= 4:
                    task_id = w
                    break
            return {"tool": "manage_tasks", "args": {"action": "complete", "task_id": task_id}}

        # ── Email Drafter ─────────────────────────────────────────────
        if any(p in t for p in [
            "escreve email", "rascunho de email", "email para", "draft email",
            "escreve um email", "redige email", "mensagem para",
        ]):
            # Heurística simples: primeiro nome depois do trigger
            body = text
            for trigger in ["escreve email", "rascunho de email", "email para", "draft email",
                            "escreve um email", "redige email", "mensagem para"]:
                body = body.lower().replace(trigger, "")
            words = body.strip().split()
            recipient = words[0] if words else "destinatario"
            subject = " ".join(words[:3]) if len(words) >= 3 else body.strip()
            return {"tool": "draft_email", "args": {
                "recipient": recipient,
                "subject": subject,
                "body": body.strip() or "(rascunho gerado pelo Hermes)",
            }}

        # ── Agent Council ──────────────────────────────────────────────
        if any(p in t for p in [
            "consulta o council", "council", "opiniao dos ais", "opinião dos ais",
            "pergunta pros ais", "pergunta pros outros", "summon the council",
            "o que acham", "o que os outros acham", "multipla perspectiva",
            "varias opiniones", "varias opiniões", "consultar council",
        ]):
            # Remove o trigger e deixa a pergunta real
            question = text.lower()
            for trigger in [
                "consulta o council", "council", "opiniao dos ais", "opinião dos ais",
                "pergunta pros ais", "pergunta pros outros", "summon the council",
                "o que acham", "o que os outros acham", "multipla perspectiva",
                "varias opiniones", "varias opiniões", "consultar council",
            ]:
                question = question.replace(trigger, "")
            return {"tool": "agent_council", "args": {"question": question.strip() or text}}

        return None

    # ── LLM Router ────────────────────────────────────────────────────

    def _load_skills_manifest(self) -> list[dict[str, Any]]:
        """Carrega skills do manifest.json."""
        manifest_path = ROOT / "hermes" / "skills" / "manifest.json"
        if not manifest_path.exists():
            return []
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return data.get("skills", [])
        except Exception:
            return []

    def _build_router_prompt(self, user_message: str) -> str:
        """Monta prompt para o LLM escolher a skill/tool correta."""
        skills = self._load_skills_manifest()
        skill_blocks = []
        for skill in skills:
            name = skill.get("name", "")
            desc = skill.get("description", "")
            examples = skill.get("examples", [])
            params = skill.get("input_schema", {})
            params_str = json.dumps(params, ensure_ascii=False) if params else "{}"
            examples_str = "\n".join(f"    - {e}" for e in examples[:3])
            skill_blocks.append(
                f"SKILL: {name}\n"
                f"  Descrição: {desc}\n"
                f"  Parâmetros: {params_str}\n"
                f"  Exemplos:\n{examples_str}"
            )

        tools_desc = """
TOOL: query_db
  Descrição: Responde perguntas sobre contagem/listagem de leads, oportunidades, reuniões, alertas.
  Parâmetros: {"query": "SQL SELECT"}
  Exemplos: quantos leads, hot leads, reuniões hoje, pipeline

TOOL: add_lead
  Descrição: Cadastra um novo lead manualmente no CRM.
  Parâmetros: {"name": "", "phone": "", "notes": ""}
  Exemplos: cadastra lead João da Silva telefone 46999999999

TOOL: manage_tasks
  Descrição: Adiciona ou lista tarefas pessoais.
  Parâmetros: {"action": "add|list", "title": ""}
  Exemplos: adiciona tarefa, lista tarefas

TOOL: direct_response
  Descrição: Use quando a mensagem for uma conversa genérica, opinião, ou não se encaixar em nenhuma skill.
  Parâmetros: {}
  Exemplos: oi, obrigado, opinião sobre o mercado
"""

        return (
            "Você é um roteador de intenções para um secretário virtual chamado Hermes.\n"
            "Analise a mensagem do usuário e escolha a skill/tool mais adequada.\n\n"
            "Mensagem do usuário:\n"
            f"{user_message}\n\n"
            "Skills disponíveis:\n\n"
            + "\n\n".join(skill_blocks)
            + "\n\n"
            + tools_desc
            + "\n\n"
            "Responda APENAS com um JSON no formato:\n"
            '{"tool": "run_skill", "args": {"skill_name": "NOME", "payload": {...}}, "confidence": 0.9}\n'
            "ou\n"
            '{"tool": "query_db", "args": {"query": "..."}, "confidence": 0.9}\n'
            "ou\n"
            '{"tool": "direct_response", "args": {}, "confidence": 0.9}\n\n'
            "Regras:\n"
            "- Use confidence entre 0.0 e 1.0.\n"
            "- Se não tiver certeza (confidence < 0.7), use direct_response.\n"
            "- NUNCA invente parâmetros que não estão na mensagem.\n"
            "- Para buscar novos leads, use skill_name 'lead_finder'.\n"
            "- Para analisar imagens, use skill_name 'vision'.\n"
        )

    def _route_with_llm(self, user_message: str) -> dict[str, Any] | None:
        """Usa LLM para decidir qual tool/skill chamar."""
        try:
            prompt = self._build_router_prompt(user_message)
            result = self.llm.extract_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=800,
            )
            parsed = result.get("parsed")
            if not parsed or not isinstance(parsed, dict):
                return None

            confidence = float(parsed.get("confidence", 0))
            if confidence < 0.7:
                return None

            tool = parsed.get("tool")
            args = parsed.get("args", {})

            # Valida tool conhecida
            valid_tools = {
                "run_skill", "query_db", "search_alerts", "add_lead", "enrich_lead", "manage_tasks",
                "cancel_meeting", "update_memory", "create_alert", "save_video_summary",
                "agent_council", "web_search", "draft_email", "direct_response",
            }
            if tool not in valid_tools:
                return None

            # Normaliza run_skill
            if tool == "run_skill" and "skill_name" in args:
                return {"tool": "run_skill", "args": args}

            return {"tool": tool, "args": args}
        except Exception as exc:
            print(f"[ROUTER] Erro: {exc}")
            return None

    # ── Formulador de Resposta Natural ────────────────────────────────

    def _formulate_natural_response(self, user_message: str, tool_name: str, tool_result: dict[str, Any]) -> str:
        """Formata resposta final para o usuário com base no resultado da tool."""
        # Para skills/tools que já entregam output formatado ou friendly, usa direto
        if tool_name == "run_skill" and tool_result.get("skill_name") in ("lead_finder", "vision"):
            return self._format_tool_result_directly(tool_name, tool_result)
        if tool_name == "enrich_lead" and tool_result.get("friendly_message"):
            return tool_result["friendly_message"]

        # Monta contexto com o resultado
        context = self._build_tool_context(tool_name, tool_result)

        # Recupera contexto convexa (grafo + vetor)
        convex_context = self.retriever.build_context_prompt(user_message, top_k=3)

        sys_msgs = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "system", "content": convex_context},
        ]

        user_content = (
            f"[Resultado da operação]\n{context}\n\n"
            "INSTRUÇÃO CRÍTICA: Você JÁ EXECUTOU a ação solicitada. "
            "Sua resposta DEVE ser baseada EXCLUSIVAMENTE no [Resultado da operação] acima. "
            "NÃO diga que não tem acesso a dados. NÃO dê respostas genéricas. "
            "Seja direto, prático e no formato adequado para Telegram.\n\n"
            f"Pergunta do usuário: {user_message}"
        )

        messages = sys_msgs + [{"role": "user", "content": user_content}]

        try:
            llm_response = self._call_llm(messages)
            return llm_response.get("content", "Pronto!").strip()
        except Exception:
            # Fallback: resposta direta com o resultado
            return self._format_tool_result_directly(tool_name, tool_result)

    def _build_tool_context(self, tool_name: str, tool_result: dict[str, Any]) -> str:
        """Monta texto descrevendo o resultado da tool para o LLM."""
        if not tool_result.get("success"):
            return f"Erro: {tool_result.get('error', 'Desconhecido')}"

        if tool_name == "query_db":
            rows = tool_result.get("rows", [])
            if not rows:
                return "Nenhum resultado encontrado no banco."
            if len(rows) == 1 and len(rows[0]) == 1:
                key = list(rows[0].keys())[0]
                return f"O resultado da consulta é: {rows[0][key]}"
            lines = []
            for i, row in enumerate(rows[:10], 1):
                parts = [f"{k}: {v}" for k, v in row.items()]
                lines.append(f"  {i}. " + " | ".join(parts))
            if len(rows) > 10:
                lines.append(f"... e mais {len(rows) - 10} resultados.")
            return "Resultados da consulta:\n" + "\n".join(lines)

        if tool_name == "search_alerts":
            rows = tool_result.get("rows", [])
            if not rows:
                return tool_result.get("message", "Nenhum alerta encontrado.")
            lines = [f"🔔 {tool_result.get('count', len(rows))} alerta(s) encontrado(s):"]
            for i, row in enumerate(rows[:10], 1):
                title = row.get("title", "Sem título")
                alert_type = row.get("alert_type", "—")
                created = row.get("created_at", "—")
                desc = row.get("description", "")[:300]
                lines.append(f"\n{i}. *{title}* ({alert_type})\n{desc}")
            return "\n".join(lines)

        if tool_name in ("run_skill", "whatsapp_analyzer", "portfolio_builder", "project_manager", "market_monitor"):
            output = tool_result.get("output", "")
            if isinstance(output, str):
                return output
            return json.dumps(output, ensure_ascii=False, indent=2)

        if tool_name == "cancel_meeting":
            return tool_result.get("message", "Reunião cancelada.")

        if tool_name == "update_memory":
            return tool_result.get("message", "Memória atualizada.")

        if tool_name == "create_alert":
            return tool_result.get("message", "Alerta criado no painel.")

        if tool_name == "save_video_summary":
            return tool_result.get("message", "Minuta de vídeo salva no CRM.")

        if tool_name == "direct_response":
            return tool_result.get("text", "")

        return str(tool_result)

    # ── Chat Livre (quando não é comando conhecido) ──────────────────

    def _chat_freely(self, user_message: str) -> str:
        """Deixa o LLM responder livremente, como uma conversa natural."""
        convex_context = self.retriever.build_context_prompt(user_message, top_k=3)

        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "system", "content": convex_context},
        ]

        # Adiciona histórico recente
        for entry in self.history[-self.max_history:]:
            messages.append({"role": "user", "content": entry["user"]})
            messages.append({"role": "assistant", "content": entry["assistant_raw"]})

        messages.append({"role": "user", "content": user_message})

        try:
            llm_response = self._call_llm(messages)
            return llm_response.get("content", "...").strip()
        except Exception as exc:
            return f"❌ Erro ao conversar: {exc}"

    # ── System Prompt ────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        return (
            "Voce e Hermes, o secretario pessoal operacional do Juan. "
            "Assessor de investimentos no escritorio 1A Investimentos, credenciado pela XP Investimentos.\n\n"
            "PERSONALIDADE: Direto, eficiente, profissional mas proximo. "
            "Nunca promete rentabilidade. Sempre honesto sobre limitacoes.\n\n"
            "REGRAS:\n"
            "- Responda de forma natural e util.\n"
            "- Se nao souber algo, admita e sugira alternativas.\n"
            "- Nunca invente dados que nao tem acesso.\n"
            "- Quando Juan pedir acoes (consultar banco, cancelar reuniao, etc), "
            "  voce ja tera o resultado da operacao — apenas formule a resposta.\n"
        )

    # ── LLM Call ─────────────────────────────────────────────────────

    def _call_llm(self, messages: list[dict[str, str]], temperature: float = 0.7, max_tokens: int = 1500) -> dict[str, Any]:
        resp = self.llm.chat(messages, temperature=temperature, max_tokens=max_tokens)
        if resp.error:
            return {"content": f"❌ Erro no LLM: {resp.error}"}
        return {"content": resp.content}

    # ── Tool Execution ────────────────────────────────────────────────

    def _execute_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool_map = {
            "direct_response": lambda a: {"success": True, "text": a.get("text", "")},
            "query_db": self._tool_query_db,
            "search_alerts": self._tool_search_alerts,
            "cancel_meeting": self._tool_cancel_meeting,
            "run_skill": self._tool_run_skill,
            "update_memory": self._tool_update_memory,
            "create_alert": self._tool_create_alert,
            "add_lead": self._tool_add_lead,
            "enrich_lead": self._tool_enrich_lead,
            "agent_council": self._tool_agent_council,
            "web_search": self._tool_web_search,
            "manage_tasks": self._tool_manage_tasks,
            "draft_email": self._tool_draft_email,
            "save_video_summary": self._tool_save_video_summary,
        }

        handler = tool_map.get(tool_name)
        if not handler:
            return {"success": False, "error": f"Ferramenta '{tool_name}' não encontrada."}

        try:
            return handler(args)
        except Exception as exc:
            traceback.print_exc()
            return {"success": False, "error": str(exc)}

    def _tool_query_db(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.secretary.tools.tool_query_db import run_query
        return run_query(args.get("query", ""))

    def _tool_search_alerts(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.secretary.tools.tool_search_alerts import search_alerts
        return search_alerts(
            query=args.get("query", ""),
            alert_type=args.get("alert_type"),
            status=args.get("status"),
            days=args.get("days"),
            limit=args.get("limit", 10),
        )

    def _tool_cancel_meeting(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.secretary.tools.tool_cancel_meeting import cancel_meeting
        return cancel_meeting(
            meeting_id=args.get("meeting_id"),
            title_fragment=args.get("title_fragment"),
            reason=args.get("reason", "Cancelado via Hermes Secretary"),
        )

    def _tool_run_skill(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.secretary.tools.tool_run_skill import run_skill
        skill_name = args.get("skill_name", "")
        payload = args.get("payload", {})

        # Vídeos/reuniões precisam de mais tempo (transcrição + LLM + OCR)
        timeout = 300 if skill_name == "video" else None
        result = run_skill(skill_name, payload, timeout=timeout)

        # Guarda preview do lead_finder para confirmação futura
        if skill_name == "lead_finder" and not payload.get("auto_save", False):
            self.last_lead_finder_result = {
                "city": payload.get("city", ""),
                "state": payload.get("state", ""),
                "segment": payload.get("segment", ""),
                "max_results": payload.get("max_results", 10),
            }
        elif skill_name == "lead_finder" and payload.get("auto_save", False):
            self.last_lead_finder_result = None

        # Guarda contexto do vídeo para salvamento posterior da minuta
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

    def _tool_save_video_summary(self, args: dict[str, Any]) -> dict[str, Any]:
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

    def _tool_update_memory(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.secretary.tools.tool_update_memory import update_memory
        return update_memory(
            action=args.get("action", "add"),
            category=args.get("category", "geral"),
            key=args.get("key", ""),
            value=args.get("value", ""),
            confidence=args.get("confidence", 0.9),
        )

    def _tool_create_alert(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.secretary.tools.tool_create_alert import create_alert
        return create_alert(
            alert_type=args.get("alert_type", "info"),
            title=args.get("title", ""),
            description=args.get("description", ""),
            lead_id=args.get("lead_id"),
            company_id=args.get("company_id"),
            suggested_action=args.get("suggested_action"),
        )

    def _tool_add_lead(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.secretary.tools.tool_add_lead import add_lead
        return add_lead(
            name=args.get("name", ""),
            phone=args.get("phone", ""),
            company=args.get("company"),
            email=args.get("email"),
            segment=args.get("segment", "outro"),
            business_line=args.get("business_line", "investimentos"),
            source=args.get("source", "hermes_telegram"),
            notes=args.get("notes"),
            city=args.get("city"),
            temperature=args.get("temperature", "warm"),
        )

    def _tool_enrich_lead(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.secretary.tools.tool_enrich_lead import enrich_lead
        return enrich_lead(
            lead_id=args.get("lead_id"),
            lead_name=args.get("lead_name"),
        )

    def _tool_agent_council(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.skills.agent_council import AgentCouncil
        council = AgentCouncil()
        question = args.get("question", "")
        if not question:
            return {"success": False, "error": "Pergunta vazia."}
        result = council.ask(question)
        synthesis_prompt = council.format_for_synthesis(result)
        # O proprio Hermes (chairman) sintetiza
        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": synthesis_prompt},
        ]
        try:
            llm_response = self._call_llm(messages)
            synthesis = llm_response.get("content", "Não consegui sintetizar.").strip()
        except Exception as exc:
            synthesis = f"[Erro na sintese: {exc}]"
        return {
            "success": True,
            "output": synthesis,
            "members": result.get("members", []),
            "job_id": result.get("job_id"),
        }

    def _tool_web_search(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.secretary.tools.tool_web_search import execute_web_search
        return execute_web_search(args.get("action", ""), args.get("query", ""))

    def _tool_manage_tasks(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.secretary.tools.tool_manage_tasks import execute_manage_tasks
        return execute_manage_tasks(
            action=args.get("action", ""),
            title=args.get("title", ""),
            due_date=args.get("due_date", ""),
            task_id=args.get("task_id", "")
        )

    def _tool_draft_email(self, args: dict[str, Any]) -> dict[str, Any]:
        from hermes.secretary.tools.tool_draft_email import execute_draft_email
        return execute_draft_email(
            recipient=args.get("recipient", ""),
            subject=args.get("subject", ""),
            body=args.get("body", "")
        )

    # ── Helpers ──────────────────────────────────────────────────────

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

    def _format_tool_result_directly(self, tool_name: str, tool_result: dict[str, Any]) -> str:
        """Formata o resultado da tool diretamente (sem LLM) — fallback rápido."""
        if not tool_result.get("success"):
            return f"⚠️ {tool_result.get('error', 'Erro')}"

        if tool_name == "query_db":
            return self._build_tool_context(tool_name, tool_result)

        if tool_name == "search_alerts":
            rows = tool_result.get("rows", [])
            if not rows:
                return tool_result.get("message", "Nenhum alerta encontrado.")
            lines = [f"🔔 {tool_result.get('count', len(rows))} alerta(s) encontrado(s):"]
            for i, row in enumerate(rows[:10], 1):
                title = row.get("title", "Sem título")
                alert_type = row.get("alert_type", "—")
                created = row.get("created_at", "—")
                desc = row.get("description", "")[:300]
                lines.append(f"\n{i}. {title} ({alert_type})\n{desc}")
            return "\n".join(lines)

        if tool_name == "run_skill":
            return tool_result.get("output", "✅ Concluído.")

        if tool_name == "cancel_meeting":
            return f"✅ {tool_result.get('message', 'Reunião cancelada.')}"

        if tool_name == "update_memory":
            return f"🧠 {tool_result.get('message', 'Salvo na memória!')}"

        if tool_name == "create_alert":
            return f"🚨 {tool_result.get('message', 'Alerta criado!')}"

        if tool_name == "add_lead":
            return f"✅ {tool_result.get('message', 'Lead cadastrado.')}\nDica: envie 'lista leads' para conferir."

        if tool_name == "direct_response":
            return tool_result.get("text", "")

        if tool_name == "agent_council":
            return f"🧠 **Agent Council**\n\n{tool_result.get('output', 'Council concluido.')[:3000]}"

        if tool_name == "web_search":
            return tool_result.get("output", "✅ Busca concluída.")

        if tool_name == "manage_tasks":
            return f"✅ {tool_result.get('message', 'Tarefa atualizada.')}"


        if tool_name == "draft_email":
            return f"📧 {tool_result.get('message', 'Rascunho criado.')}\nSalvo em: {tool_result.get('path', '')}"

        if tool_name == "save_video_summary":
            return f"💾 {tool_result.get('message', 'Minuta salva no CRM.')}" if tool_result.get("success") else f"⚠️ {tool_result.get('error', 'Erro ao salvar minuta')}"

        return "✅ Concluído."

    def _list_capabilities(self) -> str:
        return (
            "🤖 **Hermes Secretary — Seu Copiloto de Assessoria**\n\n"
            "📊 **Negócios:** leads, oportunidades, pipeline, hot leads, reuniões, alertas\n"
            "📅 **Agenda:** cancelar reuniões, ver agenda, snapshot comercial\n"
            "📱 **Conversas:** analisar conversas de cliente, sentimento, follow-up\n"
            "💼 **Carteira:** teses, análise de concentração, rebalanceamento\n"
            "📋 **Projetos:** criar e listar projetos pessoais\n"
            "🧠 **Memória:** lembrar preferências, rotinas, contatos, contexto de clientes\n"
            "🚨 **Monitoramento:** alertas proativos da operação comercial\n\n"
            "Quer testar alguma? Só mandar!"
        )
