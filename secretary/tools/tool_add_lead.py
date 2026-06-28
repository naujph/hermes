"""Tool: add_lead — Cadastra um lead rapidamente no CRM.

Recebe nome, telefone, empresa opcional, segmento e anotações.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection
from app.repositories.lead_repository import LeadRepository
from app.utils.normalizers import clean_phone


SEGMENT_MAP = {
    'clinica_medica': 'clínicas médicas',
    'advocacia': 'escritórios de advocacia',
    'contabilidade': 'escritórios de contabilidade',
    'exportadora': 'exportadoras',
    'importadora': 'importadoras',
    'circulo_proximo': 'círculo próximo',
    'outro': 'outro',
}


def resolve_persona(segment: str) -> str:
    if segment == 'clinica_medica':
        return 'decisor_clinico'
    return 'empresario_generalista'


def add_lead(
    name: str,
    phone: str,
    company: str | None = None,
    email: str | None = None,
    segment: str = 'outro',
    business_line: str = 'investimentos',
    source: str = 'hermes_telegram',
    notes: str | None = None,
    city: str | None = None,
    temperature: str = 'warm',
) -> dict:
    """Cadastra um lead no banco e retorna confirmação."""
    if not name or not name.strip():
        return {"success": False, "error": "Nome é obrigatório."}

    phone_clean = clean_phone(phone)
    if not phone_clean:
        return {"success": False, "error": "Telefone inválido."}

    now = datetime.now(UTC).isoformat()
    external_id = f"hermes_{phone_clean}_{business_line}"

    record = {
        'source': source,
        'external_id': external_id,
        'company_name': (company or name).strip(),
        'contact_name': name.strip(),
        'phone': phone_clean,
        'whatsapp_number': phone_clean,
        'email': email,
        'category': SEGMENT_MAP.get(segment, segment),
        'city': city,
        'business_line': business_line,
        'temperature': temperature,
        'conversation_status': 'em_contato',
        'outreach_status': 'primeiro_toque_realizado',
        'persona_profile': resolve_persona(segment),
        'origin_channel': 'telegram',
        'origin_campaign': 'abordagem_hermes',
        'origin_detail': segment,
        'first_touch_source': source,
        'first_touch_medium': 'hermes',
        'last_touch_source': source,
        'last_touch_medium': 'hermes',
        'last_contact_at': now,
        'next_action': 'follow_up_2_3_dias',
        'next_action_at': now,
        'approach_notes': notes,
        'qualification_notes': notes,
        'lead_nature': 'pf' if segment == 'circulo_proximo' else 'pj',
    }

    try:
        repo = LeadRepository()
        lead_id = repo.upsert(record)

        if notes:
            repo.add_interaction(
                lead_id=lead_id,
                channel='telegram',
                direction='outbound',
                message_text=notes,
                interaction_type='primeiro_toque',
                status='registrado',
                occurred_at=now,
            )

        return {
            "success": True,
            "message": f"✅ Lead cadastrado: {name} (ID {lead_id})",
            "lead_id": lead_id,
            "name": name.strip(),
            "phone": phone_clean,
            "segment": segment,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
