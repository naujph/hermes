"""Tool: enrich_lead

Enriquece um lead existente consultando CNPJá e gerando score com IA.
Se lead_id não for informado, tenta localizar lead pelo nome/empresa.
Se não encontrar, cria um lead mínimo a partir do nome e depois enriquece.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_env
from app.repositories.lead_repository import LeadRepository
from hermes.skills import enrich_lead as enrich_skill

load_env()


def _find_lead_by_name(name_fragment: str) -> dict | None:
    """Busca lead pelo nome/empresa mais parecido."""
    if not name_fragment or len(name_fragment) < 3:
        return None
    repo = LeadRepository()
    leads = repo.list_leads(search=name_fragment, limit=20)
    if not leads:
        return None
    return leads[0]


def _create_minimal_lead(name: str) -> dict:
    """Cria lead mínimo a partir de nome/empresa quando não existe no CRM."""
    from datetime import datetime, UTC
    from app.repositories.lead_repository import LeadRepository

    now = datetime.now(UTC).isoformat()
    record = {
        "source": "hermes_vision",
        "external_id": f"hermes_vision_{name.strip().replace(' ', '_').lower()}",
        "company_name": name.strip(),
        "category": "outro",
        "business_line": "investimentos",
        "temperature": "cold",
        "conversation_status": "novo",
        "outreach_status": "pendente",
        "origin_channel": "telegram",
        "origin_campaign": "vision_lead_suggestion",
        "origin_detail": "sugestao_skill_vision",
        "first_touch_source": "hermes_vision",
        "last_touch_source": "hermes_vision",
        "created_at": now,
        "updated_at": now,
    }
    repo = LeadRepository()
    lead_id = repo.upsert(record)
    lead = repo.get_lead(lead_id)
    return {"success": True, "lead_id": lead_id, "lead": lead, "created": True}


def enrich_lead(lead_id: int | None = None, lead_name: str | None = None) -> dict:
    """Enriquece um lead existente ou cria + enriquece se necessário."""
    repo = LeadRepository()
    lead = None
    created = False

    if lead_id:
        lead = repo.get_lead(lead_id)

    if not lead and lead_name:
        lead = _find_lead_by_name(lead_name)
        if lead:
            lead_id = lead.get("id")

    if not lead and lead_name:
        # Cria lead mínimo e enriquece
        create_result = _create_minimal_lead(lead_name)
        if not create_result.get("success"):
            return create_result
        lead_id = create_result.get("lead_id")
        lead = create_result.get("lead")
        created = True

    if not lead:
        return {
            "success": False,
            "error": "Não encontrei lead para enriquecer. Me diga o nome da empresa ou o lead_id.",
        }

    result = enrich_skill.main_direct(lead_id)
    result["created"] = created
    result["lead_id"] = lead_id
    result["company_name"] = lead.get("company_name") or lead_name or "Lead"

    # Traduz resposta vazia do CNPJá em mensagem útil
    if not result.get("success") or (not result.get("company_id") and not result.get("score_total")):
        result["friendly_message"] = (
            f"Não consegui enriquecer {result['company_name']} agora — "
            "a base CNPJá está indisponível ou o estabelecimento não está registrado. "
            "Mas criei o lead no CRM para você completar depois."
        )
    else:
        result["friendly_message"] = (
            f"{result['company_name']} enriquecido ✅\n"
            f"Score total: {result.get('score_total', 'N/A')} | "
            f"Company ID: {result.get('company_id', 'N/A')}"
        )
    return result

