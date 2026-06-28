"""ToolRegistry — Catálogo de ferramentas disponíveis para o orquestrador."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TOOLS: list[dict[str, Any]] = [
    {
        "name": "direct_response",
        "description": "Responde diretamente ao usuário sem executar nenhuma ação. Use para saudações, despedidas, opiniões ou quando a pergunta não exige consulta a dados.",
        "parameters": {"text": "texto da resposta"},
        "examples": ["oi", "obrigado", "o que você acha do mercado?"],
        "cost": "baixo",
    },
    {
        "name": "query_db",
        "description": "Consulta SQL segura no SQLite. Permite SELECTs para contar/listar leads, reuniões, oportunidades, comissões, alertas e hermes_alerts (notas internas, conhecimento, briefings).",
        "parameters": {"query": "string SQL SELECT"},
        "examples": [
            "quantos leads",
            "lista leads hot",
            "SELECT * FROM hermes_alerts WHERE alert_type='conhecimento' ORDER BY created_at DESC LIMIT 5",
        ],
        "cost": "baixo",
    },
    {
        "name": "search_alerts",
        "description": "Busca alertas, notas internas, conhecimento do escritório e briefings de marketing salvos no painel (tabela hermes_alerts).",
        "parameters": {
            "query": "palavras-chave (opcional)",
            "alert_type": "conhecimento|nota|oportunidade|marketing|insight|info|warning|action_required (opcional)",
            "status": "novo|visto|resolvido (opcional)",
            "days": "int (opcional)",
            "limit": "int (opcional, padrão 10)",
        },
        "examples": [
            "resumo da reunião de consórcios",
            "últimas notas internas",
            "briefings de marketing",
        ],
        "cost": "baixo",
    },
    {
        "name": "run_skill",
        "description": "Executa uma skill do Hermes (scripts em hermes/skills/). Skills disponíveis: agent_council, audio_transcribe, cockpit_digest, create_alert, daily_digest, enrich_lead, generate_briefing, lead_finder, market_monitor, parse_whatsapp, portfolio_builder, project_manager, suggest_follow_up, update_pipeline, video, vision, whatsapp_analyzer, workflow_assistant.",
        "parameters": {
            "skill_name": "nome da skill",
            "payload": "dict com argumentos da skill",
        },
        "examples": [
            "digest de hoje",
            "prospectar clínicas odontológicas em Palmas-PR",
            "analisar conversa do lead 48",
            "sugerir cortes de vídeo",
        ],
        "cost": "médio",
    },
    {
        "name": "add_lead",
        "description": "Cadastra um novo lead manualmente no CRM. Requer nome e telefone.",
        "parameters": {
            "name": "nome do contato",
            "phone": "telefone com DDD",
            "company": "nome da empresa (opcional)",
            "email": "email (opcional)",
            "segment": "categoria (opcional)",
            "notes": "anotações (opcional)",
            "city": "cidade (opcional)",
        },
        "examples": ["cadastra lead João da Silva telefone 46999999999"],
        "cost": "baixo",
    },
    {
        "name": "enrich_lead",
        "description": "Enriquece um lead existente consultando CNPJá e gerando score com IA. Pode receber lead_id ou nome da empresa.",
        "parameters": {"lead_id": "int (opcional)", "lead_name": "nome da empresa (opcional)"},
        "examples": ["enriquece a OdontoTop Palmas", "score para lead 48"],
        "cost": "alto",
    },
    {
        "name": "whatsapp_analyzer",
        "description": "Analisa conversas WhatsApp de um lead: sentimento, palavras-chave, tempo de resposta, sugestão de follow-up.",
        "parameters": {"lead_id": "int (opcional)", "phone": "str (opcional)"},
        "examples": ["analisar conversa do lead 48", "sentimento do WhatsApp da Alcast"],
        "cost": "médio",
    },
    {
        "name": "cancel_meeting",
        "description": "Cancela uma reunião agendada por ID ou fragmento do título.",
        "parameters": {"meeting_id": "int (opcional)", "title_fragment": "str (opcional)", "reason": "str (opcional)"},
        "examples": ["cancela reunião com Alcast amanhã"],
        "cost": "baixo",
    },
    {
        "name": "update_memory",
        "description": "Adiciona, atualiza ou remove fatos na memória pessoal do Juan.",
        "parameters": {"action": "add|update|delete", "category": "string", "key": "string", "value": "string"},
        "examples": ["lembra que prefiro manhã", "anota que meu cliente X gosta de RV"],
        "cost": "baixo",
    },
    {
        "name": "create_alert",
        "description": "Cria um alerta no painel Streamlit para Juan visualizar depois.",
        "parameters": {"alert_type": "string", "title": "string", "description": "string", "lead_id": "int (opcional)", "company_id": "int (opcional)", "suggested_action": "string (opcional)"},
        "examples": ["cria alerta para ligar para Alcast amanhã"],
        "cost": "baixo",
    },
    {
        "name": "manage_tasks",
        "description": "Gerencia tarefas pessoais/TODOs do Juan.",
        "parameters": {"action": "add|list|complete|delete", "title": "string (opcional)", "due_date": "YYYY-MM-DD (opcional)", "task_id": "string (opcional)"},
        "examples": ["adiciona tarefa ligar para João amanhã", "lista tarefas"],
        "cost": "baixo",
    },
    {
        "name": "draft_email",
        "description": "Cria rascunho de e-mail profissional para clientes.",
        "parameters": {"recipient": "string", "subject": "string", "body": "string"},
        "examples": ["escreve email cobrando proposta para Alcast"],
        "cost": "baixo",
    },
    {
        "name": "web_search",
        "description": "Faz busca na web ou extrai texto de URLs.",
        "parameters": {"action": "search|read_url", "query": "string"},
        "examples": ["pesquisar sobre IPCA de junho", "ler essa url https://..."],
        "cost": "médio",
    },
    {
        "name": "portfolio_builder",
        "description": "Gera teses de investimento ou análise de concentração da carteira.",
        "parameters": {"tesis_type": "string (opcional)", "valor": "number (opcional)", "acao": "string (opcional)", "horizonte": "string (opcional)"},
        "examples": ["tese 70/30 para 100000", "análise de concentração da carteira"],
        "cost": "médio",
    },
    {
        "name": "project_manager",
        "description": "Cria, lista ou atualiza projetos pessoais do Juan.",
        "parameters": {"action": "create|list|update", "name": "string (opcional)", "project_id": "int (opcional)", "status": "string (opcional)", "priority": "string (opcional)", "notes": "string (opcional)"},
        "examples": ["criar projeto Consórcio terreno", "lista projetos"],
        "cost": "baixo",
    },
    {
        "name": "agent_council",
        "description": "Consulta múltiplos LLMs e sintetiza uma opinião consensual. Use para decisões importantes.",
        "parameters": {"question": "string"},
        "examples": ["consulta o council: devo investir em BTC agora?"],
        "cost": "alto",
    },
    {
        "name": "generate_code",
        "description": "Gera script Python baseado em descrição e salva em sandbox/.",
        "parameters": {"description": "string", "language": "string (opcional)", "save_path": "string (opcional)"},
        "examples": ["gera script que calcula projeção de juros compostos"],
        "cost": "médio",
    },
    {
        "name": "run_code",
        "description": "Executa script Python previamente gerado em sandbox restrito.",
        "parameters": {"file_path": "string (opcional)", "code": "string (opcional)", "confirmed": "boolean"},
        "examples": ["roda o script sandbox/calculo.py"],
        "cost": "médio",
    },
    {
        "name": "save_video_summary",
        "description": "Persiste minuta de vídeo/reunião no CRM (interactions, meetings, opportunities, hermes_alerts).",
        "parameters": {
            "context_type": "lead|escritorio|marketing|outro",
            "minute": "dict",
            "transcript": "dict (opcional)",
            "video_path": "string",
            "caption": "string (opcional)",
            "lead_id": "int (opcional)",
        },
        "examples": ["salvar minuta como conhecimento do escritório"],
        "cost": "baixo",
    },
    {
        "name": "manage_workflows",
        "description": "Cria, atualiza, remove ou lista fluxos de trabalho (workflows) do Workflow Studio.",
        "parameters": {
            "action": "create|update|delete|list",
            "name": "string (opcional, para create/update)",
            "workflow_id": "int (opcional, para update/delete/list)",
            "definition_json": "dict|string (opcional, para create/update)",
            "description": "string (opcional)",
            "category": "string (opcional)",
        },
        "examples": [
            "criar fluxo de prospecção para Palmas-PR",
            "listar meus workflows",
            "deletar workflow 12",
        ],
        "cost": "baixo",
    },
    {
        "name": "run_workflow",
        "description": "Inicia ou controla a execução assistida de um workflow. Ações: start (inicia nova execução), next (executa nó atual), approve (aprova human_approval), cancel (cancela execução).",
        "parameters": {
            "action": "start|next|approve|cancel",
            "workflow_id": "int (obrigatório: workflow ID para start; run ID para next/approve/cancel)",
            "context_json": "dict|string (opcional, contexto inicial ou run_id)",
        },
        "examples": [
            "iniciar workflow 5",
            "executar próximo passo do workflow 8",
            "aprovar e continuar execução 10",
            "cancelar workflow 7",
        ],
        "cost": "médio",
    },
    {
        "name": "list_approvals",
        "description": "Lista pedidos de aprovação pendentes ou resolvidos do Hermes. Use quando Juan perguntar 'o que precisa de aprovação', 'aprovações pendentes' ou 'o que está esperando'.",
        "parameters": {
            "status": "pending|approved|rejected|expired|auto_executed (opcional, padrão 'pending')",
            "limit": "int (opcional, padrão 20)",
        },
        "examples": [
            "listar aprovações pendentes",
            "mostrar o que está esperando aprovação",
        ],
        "cost": "baixo",
    },
    {
        "name": "resolve_approval",
        "description": "Aprova ou rejeita um pedido do Hermes e executa a ação se aprovado. Use quando Juan disser 'aprova', 'rejeita', 'ok' ou 'pode fazer' referindo-se a uma aprovação.",
        "parameters": {
            "approval_id": "int",
            "resolution": "approved|rejected",
            "execute": "bool (opcional, padrão true)",
        },
        "examples": [
            "aprova o pedido 12",
            "rejeita aprovacao 3",
        ],
        "cost": "baixo",
    },
]


def get_registry_text() -> str:
    """Retorna descrição das tools formatada para prompts."""
    lines = ["### FERRAMENTAS DISPONÍVEIS"]
    for tool in TOOLS:
        lines.append(f"\n**{tool['name']}** — {tool['description']}")
        lines.append(f"Parâmetros: {json.dumps(tool['parameters'], ensure_ascii=False)}")
        if tool.get("examples"):
            lines.append(f"Exemplos: {', '.join(tool['examples'])}")
    return "\n".join(lines)


def get_tool(name: str) -> dict[str, Any] | None:
    for tool in TOOLS:
        if tool["name"] == name:
            return tool
    return None
