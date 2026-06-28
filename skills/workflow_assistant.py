#!/usr/bin/env python3
"""Skill: workflow_assistant

Gera um definition_json de workflow a partir de uma descrição em linguagem natural.
Usa UnifiedLLMClient.extract_json() com fallback para um fluxo mínimo sempre válido.

Entrada (stdin): {"description": "...", "name": "..." (opcional), "category": "..." (opcional)}
Saída (stdout): {"success": bool, "name": str, "description": str, "definition_json": {...}, "message": str}
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import load_env
from app.llm_client import UnifiedLLMClient

load_env()

SYSTEM_PROMPT = (
    "Você é o assistente de workflows do Hermes, secretário operacional do Juan.\n"
    "Seu trabalho é converter uma descrição em linguagem natural em um fluxo de trabalho "
    "estruturado para o Workflow Studio do Lead Prospecting Engine.\n"
    "Regras:\n"
    "- Use APENAS os tipos de nó: start, end, skill, tool, condition, human_approval, notification.\n"
    "- Todo workflow DEVE ter um nó 'start' e um nó 'end'.\n"
    "- Nós de skill devem usar skill_name e payload no config.\n"
    "- Nós de tool devem usar tool_name e args no config.\n"
    "- Nós de human_approval devem ter uma mensagem clara no config.\n"
    "- Nós de notification devem ter title, description e alert_type no config.\n"
    "- Nós de condition devem ter expression no config e edges com label 'true'/'false'.\n"
    "- Skills disponíveis: lead_finder, enrich_lead, suggest_follow_up, cockpit_digest, daily_digest, market_monitor, vision, video, whatsapp_analyzer, portfolio_builder, project_manager, agent_council, create_alert.\n"
    "- Tools disponíveis: create_alert, manage_tasks, add_lead, enrich_lead, query_db, search_alerts, draft_email, web_search.\n"
    "- Use placeholders {{node_id.output.campo}} quando um nó depende do output de outro.\n"
    "- NUNCA prometa rentabilidade. Respeite CVM/XP.\n"
    "- Responda APENAS com o JSON do schema. NÃO use markdown."
)

SCHEMA_HINT = {
    "name": "string (título curto do workflow)",
    "description": "string (descrição do objetivo)",
    "category": "string (prospecção, rotina, executivo, etc.)",
    "definition_json": {
        "nodes": [
            {
                "id": "string (snake_case único)",
                "type": "start | skill | tool | condition | human_approval | notification | end",
                "label": "string (rótulo amigável)",
                "config": "dict com parâmetros do nó",
            }
        ],
        "edges": [
            {
                "source": "id do nó origem",
                "target": "id do nó destino",
                "label": "string ou null (obrigatório para condition: 'true'/'false')",
            }
        ],
    },
}

USER_PROMPT_TEMPLATE = """Converta a descrição abaixo em um workflow estruturado.

Descrição: {description}

Requisitos:
1. Mínimo 3 nós (além de start e end).
2. Se houver decisão humana, inclua human_approval.
3. Se houver condicional, use node type 'condition' com expression e edges true/false.
4. Skills do Hermes devem ter config: {{"skill_name": "...", "payload": {{...}}}}.
5. Tools do Hermes devem ter config: {{"tool_name": "...", "args": {{...}}}}.
6. Notificações devem ter config: {{"title": "...", "description": "...", "alert_type": "..."}}.
7. Use IDs curtos, descritivos e snake_case.
8. O workflow deve ser coerente e executável pelo WorkflowEngine.

Responda APENAS com JSON no schema indicado."""


def _build_prompt(description: str) -> str:
    return USER_PROMPT_TEMPLATE.replace("{description}", description)


def _fallback_workflow(description: str, name: str | None = None, category: str | None = None) -> dict[str, Any]:
    """Fallback 100% estruturado quando a LLM não retorna JSON utilizável."""
    description_lower = (description or "").lower()
    default_name = name or "Novo fluxo"
    default_category = category or "geral"

    nodes = [
        {"id": "start", "type": "start", "label": "Início", "config": {}},
        {
            "id": "human_approval",
            "type": "human_approval",
            "label": "Validar próximo passo",
            "config": {"message": f"Fluxo '{default_name}' foi iniciado. Aprovar execução?"},
        },
        {"id": "end", "type": "end", "label": "Fim", "config": {}},
    ]
    edges = [
        {"source": "start", "target": "human_approval"},
        {"source": "human_approval", "target": "end"},
    ]

    # Tenta inferir pelo menos um nó útil a partir de palavras-chave
    if any(k in description_lower for k in ["prospect", "lead", "google places", "clínica", "médico", "advogado"]):
        nodes.insert(
            1,
            {
                "id": "find_leads",
                "type": "skill",
                "label": "Buscar leads",
                "config": {
                    "skill_name": "lead_finder",
                    "payload": {
                        "city": "{{city}}",
                        "state": "{{state}}",
                        "segment": "{{segment}}",
                        "max_results": 10,
                        "auto_save": False,
                    },
                },
            },
        )
        nodes[2]["config"]["message"] = "Leads encontrados. Aprovar enriquecimento?"
        edges = [
            {"source": "start", "target": "find_leads"},
            {"source": "find_leads", "target": "human_approval"},
            {"source": "human_approval", "target": "end"},
        ]
    elif any(k in description_lower for k in ["follow", "follow-up", "retomar", "reengajar"]):
        nodes.insert(
            1,
            {
                "id": "suggest_follow_up",
                "type": "skill",
                "label": "Sugerir follow-ups",
                "config": {
                    "skill_name": "suggest_follow_up",
                    "payload": {"days_since_last_contact": 3},
                },
            },
        )
        nodes[2]["config"]["message"] = "Sugestões de follow-up geradas. Criar alertas?"
        edges = [
            {"source": "start", "target": "suggest_follow_up"},
            {"source": "suggest_follow_up", "target": "human_approval"},
            {"source": "human_approval", "target": "end"},
        ]
    elif any(k in description_lower for k in ["digest", "briefing", "resumo", "executivo", "cockpit"]):
        nodes.insert(
            1,
            {
                "id": "cockpit_digest",
                "type": "skill",
                "label": "Gerar digest executivo",
                "config": {"skill_name": "cockpit_digest", "payload": {}},
            },
        )
        nodes.insert(
            3,
            {
                "id": "notify",
                "type": "notification",
                "label": "Notificar no painel",
                "config": {
                    "title": "Digest executivo",
                    "description": "{{cockpit_digest.output.briefing}}",
                    "alert_type": "insight",
                    "suggested_action": "Abrir o Cockpit e revisar prioridades.",
                },
            },
        )
        nodes[2]["config"]["message"] = "Digest gerado. Criar notificação no painel?"
        edges = [
            {"source": "start", "target": "cockpit_digest"},
            {"source": "cockpit_digest", "target": "human_approval"},
            {"source": "human_approval", "target": "notify"},
            {"source": "notify", "target": "end"},
        ]

    return {
        "name": default_name,
        "description": description or f"Workflow criado em {datetime.now(UTC).isoformat()}",
        "category": default_category,
        "definition_json": {"nodes": nodes, "edges": edges},
    }


def _normalize_definition(parsed: dict[str, Any], description: str) -> dict[str, Any]:
    """Garante que a definição gerada tenha estrutura mínima válida."""
    name = parsed.get("name") or "Novo fluxo"
    category = parsed.get("category") or "geral"
    definition = parsed.get("definition_json") or parsed

    if not isinstance(definition, dict):
        return _fallback_workflow(description, name, category)

    nodes = definition.get("nodes", [])
    edges = definition.get("edges", [])

    if not isinstance(nodes, list) or not nodes:
        return _fallback_workflow(description, name, category)

    # Garante start e end
    has_start = any(n.get("type") == "start" for n in nodes)
    has_end = any(n.get("type") == "end" for n in nodes)

    if not has_start:
        nodes.insert(0, {"id": "start", "type": "start", "label": "Início", "config": {}})
    if not has_end:
        nodes.append({"id": "end", "type": "end", "label": "Fim", "config": {}})

    # Garante IDs únicos
    seen_ids: set[str] = set()
    for i, node in enumerate(nodes):
        node_id = str(node.get("id") or f"node_{i}")
        if node_id in seen_ids:
            node_id = f"{node_id}_{i}"
        seen_ids.add(node_id)
        node["id"] = node_id

    # Normaliza edges
    normalized_edges: list[dict[str, Any]] = []
    if isinstance(edges, list):
        for edge in edges:
            if isinstance(edge, dict) and edge.get("source") in seen_ids and edge.get("target") in seen_ids:
                normalized_edges.append({
                    "source": edge["source"],
                    "target": edge["target"],
                    "label": edge.get("label") or edge.get("condition") or None,
                })

    # Se não houver edges, conecta sequencialmente
    if not normalized_edges and len(nodes) > 1:
        for i in range(len(nodes) - 1):
            normalized_edges.append({"source": nodes[i]["id"], "target": nodes[i + 1]["id"]})

    return {
        "name": name,
        "description": parsed.get("description") or description,
        "category": category,
        "definition_json": {"nodes": nodes, "edges": normalized_edges},
    }


def generate_workflow(description: str, name: str | None = None, category: str | None = None) -> dict[str, Any]:
    llm = UnifiedLLMClient(timeout=180)
    prompt = _build_prompt(description)

    result = llm.extract_json(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=3000,
        schema_hint=SCHEMA_HINT,
    )

    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        parsed = {}

    normalized = _normalize_definition(parsed, description)

    # Se a LLM devolveu estrutura vazia/inalcançável, usa fallback
    has_nodes = bool(normalized["definition_json"].get("nodes"))
    has_content = has_nodes and len(normalized["definition_json"]["nodes"]) > 2
    if not has_content:
        normalized = _fallback_workflow(description, name, category)

    if name:
        normalized["name"] = name
    if category:
        normalized["category"] = category

    return {
        "success": True,
        "name": normalized["name"],
        "description": normalized["description"],
        "category": normalized["category"],
        "definition_json": normalized["definition_json"],
        "message": f"Workflow '{normalized['name']}' gerado com {len(normalized['definition_json']['nodes'])} nós.",
        "llm_used": result.get("raw") is not None,
        "llm_error": result.get("error"),
    }


def main() -> None:
    try:
        if sys.stdin.isatty():
            payload = {"description": ""}
        else:
            raw = sys.stdin.buffer.read().decode("utf-8")
            payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "error": "JSON inválido na entrada"}, ensure_ascii=False))
        sys.exit(1)

    description = str(payload.get("description", "")).strip()
    if not description:
        print(json.dumps({"success": False, "error": "Descrição é obrigatória"}, ensure_ascii=False))
        sys.exit(1)

    result = generate_workflow(
        description=description,
        name=payload.get("name") or None,
        category=payload.get("category") or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
