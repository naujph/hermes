#!/usr/bin/env python3
"""Skill: enrich_lead

Enriquece um lead existente consultando CNPJá e gerando score com IA.
Recebe JSON via stdin, retorna JSON via stdout.

Uso:
    echo '{"lead_id": 1}' | python hermes/skills/enrich_lead.py
"""
import json
import os
import sys
from pathlib import Path

# Adiciona o projeto ao path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.orchestrator import EnrichmentOrchestrator
from app.database import get_connection
from app.repositories.lead_repository import LeadRepository


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "errors": ["JSON inválido na entrada"]}))
        sys.exit(1)

    lead_id = payload.get('lead_id')
    skip_ai = payload.get('skip_ai', False)

    if not lead_id:
        print(json.dumps({"success": False, "errors": ["lead_id é obrigatório"]}))
        sys.exit(1)

    repo = LeadRepository()
    lead = repo.get_lead(lead_id)
    if not lead:
        print(json.dumps({"success": False, "errors": [f"Lead {lead_id} não encontrado"]}))
        sys.exit(1)

    # Monta payload para o orquestrador
    context = {
        'lead_payload': {
            'company_name': lead.get('company_name'),
            'phone': lead.get('phone'),
            'website': lead.get('website'),
            'category': lead.get('category'),
            'city': lead.get('city'),
            'state': lead.get('state'),
            'neighborhood': lead.get('neighborhood'),
            'rating': lead.get('rating'),
            'review_count': lead.get('review_count', 0),
            'external_id': lead.get('external_id'),
            'source': lead.get('source', 'manual'),
            'source_batch': lead.get('source_batch'),
            'email': lead.get('email'),
            'contact_name': lead.get('contact_name'),
        },
        'lead_id': lead_id,
        'skip_ai': skip_ai,
    }

    orchestrator = EnrichmentOrchestrator()
    result = orchestrator.run(context)

    # Saída JSON
    print(json.dumps(result, ensure_ascii=False, default=str))


def main_direct(lead_id: int, skip_ai: bool = False) -> dict:
    """Versão programática do enriquecimento, usada por tools."""
    repo = LeadRepository()
    lead = repo.get_lead(lead_id)
    if not lead:
        return {"success": False, "errors": [f"Lead {lead_id} não encontrado"]}

    context = {
        'lead_payload': {
            'company_name': lead.get('company_name'),
            'phone': lead.get('phone'),
            'website': lead.get('website'),
            'category': lead.get('category'),
            'city': lead.get('city'),
            'state': lead.get('state'),
            'neighborhood': lead.get('neighborhood'),
            'rating': lead.get('rating'),
            'review_count': lead.get('review_count', 0),
            'external_id': lead.get('external_id'),
            'source': lead.get('source', 'manual'),
            'source_batch': lead.get('source_batch'),
            'email': lead.get('email'),
            'contact_name': lead.get('contact_name'),
        },
        'lead_id': lead_id,
        'skip_ai': skip_ai,
    }

    orchestrator = EnrichmentOrchestrator()
    return orchestrator.run(context)


if __name__ == '__main__':
    main()
