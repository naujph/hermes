#!/usr/bin/env python3
"""Skill: parse_whatsapp

Analisa mensagem recebida via WhatsApp e vincula a lead existente ou detecta novo.
Recebe JSON via stdin, retorna JSON via stdout.
"""
import json
import sys
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection
from app.repositories.lead_repository import LeadRepository
from app.utils.normalizers import clean_phone


def find_lead_by_phone(phone: str) -> dict | None:
    clean = clean_phone(phone)
    if not clean:
        return None
    with get_connection() as conn:
        # Busca por telefone ou whatsapp_number
        row = conn.execute(
            "SELECT * FROM leads WHERE REPLACE(REPLACE(REPLACE(phone, '(', ''), ')', ''), '-', '') LIKE ? OR REPLACE(REPLACE(REPLACE(whatsapp_number, '(', ''), ')', ''), '-', '') LIKE ? LIMIT 1",
            (f'%{clean}%', f'%{clean}%'),
        ).fetchone()
        return dict(row) if row else None


def classify_message(text: str) -> str:
    t = text.lower()
    urgent = ['urgente', 'emergencia', 'preciso hoje', 'agora', 'cancelar', 'reclamação']
    interest = ['interessado', 'quero saber mais', 'pode me explicar', 'funciona como', 'valor', 'preço', 'quanto custa']
    rejection = ['não quero', 'não tenho interesse', 'pare de mandar', 'remova']

    if any(u in t for u in urgent):
        return 'urgent'
    if any(r in t for r in rejection):
        return 'rejection'
    if any(i in t for i in interest):
        return 'interest'
    return 'neutral'


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "errors": ["JSON inválido"]}))
        sys.exit(1)

    phone = payload.get('phone')
    message = payload.get('message_text', '')
    timestamp = payload.get('timestamp') or datetime.now(UTC).isoformat()

    if not phone:
        print(json.dumps({"success": False, "errors": ["phone é obrigatório"]}))
        sys.exit(1)

    lead = find_lead_by_phone(phone)
    classification = classify_message(message)

    if lead:
        lead_id = lead['id']
        repo = LeadRepository()
        repo.add_interaction(
            lead_id=lead_id,
            channel='whatsapp',
            direction='inbound',
            message_text=message,
            interaction_type='mensagem_recebida',
            status='registrado',
            occurred_at=timestamp,
        )

        # Atualiza status se for interesse ou urgência
        if classification == 'interest':
            repo.update_lead(lead_id, {'conversation_status': 'respondeu', 'temperature': 'warm'})
        elif classification == 'urgent':
            repo.update_lead(lead_id, {'conversation_status': 'em_contato', 'temperature': 'hot'})

        action = 'update_lead'
        suggested = None
        if classification == 'interest':
            suggested = f"Obrigado pelo interesse! Posso agendar uma conversa rápida de 15 minutos para entender melhor seu cenário?"
        elif classification == 'urgent':
            suggested = f"Entendido, vou priorizar. Me confirma qual é a urgência exata para eu direcionar corretamente?"
    else:
        lead_id = None
        action = 'new_lead'
        suggested = None

    print(json.dumps({
        "success": True,
        "lead_id": lead_id,
        "action": action,
        "classification": classification,
        "suggested_response": suggested,
        "logs": [f"Mensagem de {phone} classificada como {classification}"],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
