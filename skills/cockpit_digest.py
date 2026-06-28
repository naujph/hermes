#!/usr/bin/env python3
"""
Skill: cockpit_digest

Gera o briefing executivo do Super Secretário Hermes para o Cockpit.
Consolida CRM, alertas pendentes, memória convexa, memória pessoal e agenda
em um JSON estruturado pronto para renderização na UI.

Ações suportadas:
- generate: gera o briefing completo (padrão)
- regenerate: força regeneração ignorando cache
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, UTC, timedelta
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

# ── Prompts ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "Você é Hermes, o super secretário do Juan, assessor de investimentos no "
    "escritório 1A Investimentos credenciado pela XP Investimentos.\n"
    "Seu trabalho é analisar todos os dados disponíveis e entregar um briefing "
    "executivo claro, direto e acionável para o Cockpit do Juan.\n"
    "- NUNCA prometa rentabilidade.\n"
    "- NUNCA dê recomendação de investimento genérica.\n"
    "- Foque em decisões, próximos passos e informações acionáveis.\n"
    "- Respeite o enquadramento regulatório da CVM/XP.\n"
    "- Seja enxuto. Juan prefere output executivo direto.\n"
    "- Responda APENAS com um objeto JSON. NÃO use markdown fora do JSON.\n"
    "- Use EXATAMENTE as chaves do schema em INGLÊS. NUNCA traduza as chaves.\n"
    "- Seja conciso: resumo em 2-4 frases, até 5 prioridades, até 3 sugestões. Textos curtos."
)

USER_PROMPT_TEMPLATE = """Estado da operação do Juan. Gere JSON no schema exato.

CRM: {snapshot}

Alertas: {alerts}

Reuniões: {meetings}

Oportunidades: {opportunities}

Memória pessoal: {personal_memory}

Memória convexa: {convex_context}

Interações recentes: {recent_interactions}

Regras:
- JSON válido, sem markdown, em UMA linha ou formatado.
- Chaves em INGLÊS: executive_summary, priorities, hermes_suggestions, next_best_actions.
- Até 5 prioridades MUITO curtas (rank, title, why, action, data_source, entity_id, entity_type).
- Até 3 sugestões do Hermes.
- Até 3 next_best_actions.
- Resumo em 2-4 frases.
- Seja EXTREMAMENTE enxuto. Textos curtos.

Schema: {"executive_summary":"string","priorities":[{"rank":"int","title":"string","why":"string","action":"string","data_source":"string","entity_id":"int|null","entity_type":"string"}],"hermes_suggestions":["string"],"next_best_actions":["string"]}
"""

COCKPIT_SCHEMA_HINT = {
    "executive_summary": "string (2-4 frases com o essencial)",
    "priorities": [
        {
            "rank": "integer",
            "title": "string",
            "why": "string",
            "action": "string",
            "data_source": "string (crm|convex|alerts|memory|calendar)",
            "entity_id": "integer or string (id do lead/alerta/oportunidade, opcional)",
            "entity_type": "string (lead|alert|opportunity|meeting|knowledge, opcional)",
        }
    ],
    "alerts": [
        {
            "id": "integer or null",
            "title": "string",
            "type": "string",
            "description": "string",
            "suggested_action": "string",
            "status": "string",
        }
    ],
    "follow_ups": [
        {
            "lead_id": "integer or null",
            "company_name": "string",
            "channel": "string",
            "suggested_message": "string",
            "reason": "string",
        }
    ],
    "meetings": [
        {
            "id": "integer or null",
            "title": "string",
            "company_name": "string",
            "scheduled_start": "string",
            "provider": "string",
        }
    ],
    "opportunities": [
        {
            "id": "integer or null",
            "title": "string",
            "stage": "string",
            "estimated_value": "number or null",
            "company_name": "string",
        }
    ],
    "hermes_suggestions": ["string"],
    "next_best_actions": ["string"],
}

# ── Data collectors ─────────────────────────────────────────────────────


def _get_crm_snapshot() -> dict[str, Any]:
    from app.hermes_bridge import get_system_snapshot
    try:
        return get_system_snapshot()
    except Exception as exc:
        return {"error": str(exc), "metrics": {}}


def _get_pending_alerts(limit: int = 5) -> list[dict[str, Any]]:
    from app.hermes_bridge import list_pending_alerts
    try:
        return list_pending_alerts(status="novo", limit=limit)
    except Exception as exc:
        return [{"error": str(exc)}]


def _get_todays_meetings() -> list[dict[str, Any]]:
    from app.database import get_connection
    from app.utils.formatters import format_datetime
    try:
        with get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT m.*, l.company_name
                FROM meetings m
                LEFT JOIN leads l ON l.id = m.lead_id
                WHERE DATE(m.scheduled_start) IN (DATE('now'), DATE('now', '+1 day'))
                ORDER BY m.scheduled_start ASC
                LIMIT 50
                '''
            ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "company_name": row["company_name"] or "—",
                "scheduled_start": format_datetime(row["scheduled_start"]),
                "provider": row["meeting_provider"] or "—",
                "status": row["meeting_status"] or "—",
            }
            for row in rows
        ]
    except Exception as exc:
        return [{"error": str(exc)}]


def _get_active_opportunities(limit: int = 20) -> list[dict[str, Any]]:
    from app.database import get_connection
    try:
        with get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT o.*, l.company_name
                FROM opportunities o
                LEFT JOIN leads l ON l.id = o.lead_id
                WHERE o.status = 'aberta'
                ORDER BY o.estimated_value DESC
                LIMIT ?
                ''',
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "stage": row["stage"] or "—",
                "estimated_value": row["estimated_value"],
                "company_name": row["company_name"] or "—",
            }
            for row in rows
        ]
    except Exception as exc:
        return [{"error": str(exc)}]


def _get_recent_interactions(limit: int = 5) -> list[dict[str, Any]]:
    from app.database import get_connection
    from app.utils.formatters import format_datetime
    try:
        with get_connection() as conn:
            rows = conn.execute(
                '''
                SELECT i.*, l.company_name
                FROM interactions i
                LEFT JOIN leads l ON l.id = i.lead_id
                ORDER BY i.occurred_at DESC
                LIMIT ?
                ''',
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "company_name": row["company_name"] or "—",
                "channel": row["channel"],
                "direction": row["direction"],
                "message_text": (row["message_text"] or "")[:200],
                "interaction_type": row["interaction_type"],
                "status": row["status"],
                "occurred_at": format_datetime(row["occurred_at"]),
            }
            for row in rows
        ]
    except Exception as exc:
        return [{"error": str(exc)}]


def _get_personal_memory_text() -> str:
    from hermes.secretary.context.personal_memory import PersonalMemory
    try:
        mem = PersonalMemory()
        parts = [
            mem.get_profile_text(),
            mem.get_preferences_text(),
            mem.get_projects_text(),
        ]
        # Adiciona fatos importantes
        facts = mem.list_facts()
        if facts:
            fact_lines = ["### FATOS RELEVANTES"]
            for f in facts[-15:]:
                fact_lines.append(f"[{f.get('category', '')}] {f.get('key', '')}: {f.get('value', '')}")
            parts.append("\n".join(fact_lines))
        return "\n\n".join(p for p in parts if p and p.strip())
    except Exception as exc:
        return f"[Erro memória pessoal: {exc}]"


def _get_convex_context(query: str = "prioridades comerciais de hoje") -> str:
    from hermes.memory.retriever import Retriever
    try:
        retriever = Retriever()
        ctx = retriever.build_context_prompt(query, top_k=3)
        # Limita para não estourar o contexto do modelo local
        if len(ctx) > 2500:
            ctx = ctx[:2500] + "\n[Contexto convexa truncado por tamanho]"
        return ctx
    except Exception as exc:
        return f"[Erro memória convexa: {exc}]"


# ── LLM generation ──────────────────────────────────────────────────────


def _build_prompt(
    snapshot: dict[str, Any],
    alerts: list[dict[str, Any]],
    meetings: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    personal_memory: str,
    convex_context: str,
    recent_interactions: list[dict[str, Any]],
) -> str:
    return (
        USER_PROMPT_TEMPLATE
        .replace("{snapshot}", json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
        .replace("{alerts}", json.dumps(alerts, ensure_ascii=False, indent=2, default=str))
        .replace("{meetings}", json.dumps(meetings, ensure_ascii=False, indent=2, default=str))
        .replace("{opportunities}", json.dumps(opportunities, ensure_ascii=False, indent=2, default=str))
        .replace("{personal_memory}", personal_memory or "Nenhuma memória pessoal relevante.")
        .replace("{convex_context}", convex_context or "Nenhum contexto da memória convexa.")
        .replace("{recent_interactions}", json.dumps(recent_interactions, ensure_ascii=False, indent=2, default=str))
    )


def _build_fallback_briefing(
    snapshot: dict[str, Any],
    alerts: list[dict[str, Any]],
    meetings: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    recent_interactions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fallback 100% data-driven caso a LLM não retorne JSON utilizável."""
    total_leads = snapshot.get("metrics", {}).get("total_leads", 0)
    hot_leads = snapshot.get("metrics", {}).get("hot_leads", 0)
    pending_alerts = snapshot.get("metrics", {}).get("pending_alerts", 0)
    today_meetings = snapshot.get("metrics", {}).get("today_meetings", 0)

    summary_parts = [f"Base tem {total_leads} leads cadastrados"]
    if hot_leads:
        summary_parts.append(f"{hot_leads} lead(s) quente(s)")
    if pending_alerts:
        summary_parts.append(f"{pending_alerts} alerta(s) pendente(s)")
    if today_meetings:
        summary_parts.append(f"{today_meetings} reunião(ões) hoje")

    summary = " | ".join(summary_parts)

    priorities: list[dict[str, Any]] = []
    if hot_leads and snapshot.get("hot_leads"):
        lead = snapshot["hot_leads"][0]
        priorities.append(
            {
                "rank": 1,
                "title": f"Acompanhar lead quente: {lead.get('company_name', 'desconhecido')}",
                "why": "Lead marcado como hot no CRM.",
                "action": f"Entrar em contato via {lead.get('ideal_channel', 'canal ideal')}.",
                "data_source": "crm",
                "entity_id": lead.get("id"),
                "entity_type": "lead",
            }
        )
    if pending_alerts and alerts:
        a = alerts[0]
        priorities.append(
            {
                "rank": len(priorities) + 1,
                "title": f"Resolver alerta: {a.get('title', 'pendente')}",
                "why": a.get("description", "Alerta sem descrição."),
                "action": a.get("suggested_action", "Revisar alerta no cockpit."),
                "data_source": "alerts",
                "entity_id": a.get("id"),
                "entity_type": "alert",
            }
        )
    if today_meetings and meetings:
        m = meetings[0]
        priorities.append(
            {
                "rank": len(priorities) + 1,
                "title": f"Preparar reunião: {m.get('title', '—')}",
                "why": "Reunião agendada para hoje/amanhã.",
                "action": "Revisar histórico do lead e pauta.",
                "data_source": "calendar",
                "entity_id": m.get("id"),
                "entity_type": "meeting",
            }
        )
    if not priorities:
        priorities.append(
            {
                "rank": 1,
                "title": "Prospectar novos leads",
                "why": "Sem leads quentes, alertas ou reuniões iminentes.",
                "action": "Usar a coleta do cockpit para abastecer a base.",
                "data_source": "crm",
                "entity_id": None,
                "entity_type": "lead",
            }
        )

    follow_ups: list[dict[str, Any]] = []
    for row in recent_interactions[:3]:
        company = row.get("company_name") or "Lead"
        channel = row.get("channel") or "whatsapp"
        follow_ups.append(
            {
                "lead_id": row.get("lead_id"),
                "company_name": company,
                "channel": channel,
                "suggested_message": "",
                "reason": f"Interação recente em {row.get('interaction_type', '—')}.",
            }
        )

    return {
        "executive_summary": summary,
        "priorities": priorities,
        "alerts": [
            {
                "id": a.get("id"),
                "title": a.get("title", "—"),
                "type": a.get("type", "—"),
                "description": a.get("description", "—"),
                "suggested_action": a.get("suggested_action", "—"),
                "status": a.get("status", "novo"),
            }
            for a in alerts[:10]
        ],
        "follow_ups": follow_ups,
        "meetings": [
            {
                "id": m.get("id"),
                "title": m.get("title", "—"),
                "company_name": m.get("company_name", "—"),
                "scheduled_start": str(m.get("scheduled_start") or ""),
                "provider": m.get("meeting_provider", "—"),
            }
            for m in meetings[:20]
        ],
        "opportunities": [
            {
                "id": o.get("id"),
                "title": o.get("title", "—"),
                "stage": o.get("stage", "—"),
                "estimated_value": o.get("estimated_value"),
                "company_name": o.get("company_name", "—"),
            }
            for o in opportunities[:20]
        ],
        "hermes_suggestions": [
            "Verificar se leads de follow-up antigo merecem novo toque.",
            "Revisar alertas pendentes antes de iniciar prospecção fria.",
        ],
        "next_best_actions": [
            "Abrir o cockpit e priorizar leads hot.",
            "Revisar alertas e decidir aceite/descarte.",
        ],
    }


def _generate_briefing(
    snapshot: dict[str, Any],
    alerts: list[dict[str, Any]],
    meetings: list[dict[str, Any]],
    opportunities: list[dict[str, Any]],
    personal_memory: str,
    convex_context: str,
    recent_interactions: list[dict[str, Any]],
) -> dict[str, Any]:
    llm = UnifiedLLMClient(timeout=180)
    prompt = _build_prompt(snapshot, alerts, meetings, opportunities, personal_memory, convex_context, recent_interactions)

    result = llm.extract_json(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=4000,
        schema_hint=None,
    )
    parsed_raw = result.get("parsed")

    # Normaliza: se a LLM devolveu só a lista de prioridades, empacota
    if isinstance(parsed_raw, list):
        parsed_raw = {"priorities": parsed_raw}

    parsed: dict[str, Any] = parsed_raw if isinstance(parsed_raw, dict) else {}

    fallback = _build_fallback_briefing(
        snapshot=snapshot,
        alerts=alerts,
        meetings=meetings,
        opportunities=opportunities,
        recent_interactions=recent_interactions,
    )

    # Se a LLM devolveu estrutura vazia/inalcançável, usa fallback 100% data-driven
    has_llm_content = (
        parsed.get("executive_summary", "") not in ("", "Não foi possível gerar o resumo executivo.")
        or parsed.get("priorities")
        or parsed.get("alerts")
        or parsed.get("follow_ups")
        or parsed.get("meetings")
        or parsed.get("opportunities")
    )
    if not has_llm_content:
        return fallback

    # Mescla: LLM ganha, campos faltantes vêm do fallback
    for key in fallback:
        if key not in parsed or not parsed[key]:
            parsed[key] = fallback[key]

    # Garante campos mínimos
    parsed.setdefault("executive_summary", fallback["executive_summary"])
    parsed.setdefault("priorities", [])
    parsed.setdefault("alerts", [])
    parsed.setdefault("follow_ups", [])
    parsed.setdefault("meetings", [])
    parsed.setdefault("opportunities", [])
    parsed.setdefault("hermes_suggestions", [])
    parsed.setdefault("next_best_actions", [])

    # Normaliza prioridades com rank
    for idx, p in enumerate(parsed["priorities"], start=1):
        p["rank"] = p.get("rank") or idx

    return parsed


# ── Cache ───────────────────────────────────────────────────────────────


def _cache_dir() -> Path:
    d = Path.home() / "AppData" / "Local" / "lead_prospecting_engine" / "cockpit_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_cached() -> dict[str, Any] | None:
    cache_file = _cache_dir() / "digest_cache.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        generated_at = data.get("generated_at", "")
        if generated_at:
            dt = datetime.fromisoformat(generated_at)
            if datetime.now(UTC) - dt < timedelta(minutes=30):
                return data
        return None
    except Exception:
        return None


def _save_cache(data: dict[str, Any]) -> None:
    cache_file = _cache_dir() / "digest_cache.json"
    data["generated_at"] = datetime.now(UTC).isoformat()
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# ── Main entrypoints ────────────────────────────────────────────────────


def generate_digest(skip_cache: bool = False) -> dict[str, Any]:
    if not skip_cache:
        cached = _load_cached()
        if cached:
            cached["cached"] = True
            return cached

    snapshot = _get_crm_snapshot()
    alerts = _get_pending_alerts()
    meetings = _get_todays_meetings()
    opportunities = _get_active_opportunities()
    personal_memory = _get_personal_memory_text()
    convex_context = _get_convex_context()
    recent_interactions = _get_recent_interactions()

    briefing = _generate_briefing(
        snapshot=snapshot,
        alerts=alerts,
        meetings=meetings,
        opportunities=opportunities,
        personal_memory=personal_memory,
        convex_context=convex_context,
        recent_interactions=recent_interactions,
    )

    result = {
        "success": True,
        "cached": False,
        "generated_at": datetime.now(UTC).isoformat(),
        "data_sources": {
            "crm": True,
            "alerts": len(alerts) > 0,
            "meetings": len(meetings) > 0,
            "opportunities": len(opportunities) > 0,
            "personal_memory": bool(personal_memory.strip()),
            "convex": bool(convex_context.strip()),
        },
        "briefing": briefing,
    }
    _save_cache(result)
    return result


def main() -> None:
    try:
        if sys.stdin.isatty():
            payload = {}
        else:
            raw = sys.stdin.buffer.read().decode("utf-8")
            payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "error": "JSON inválido na entrada"}))
        sys.exit(1)

    action = payload.get("action", "generate")
    skip_cache = action in ("regenerate", "refresh")

    result = generate_digest(skip_cache=skip_cache)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
