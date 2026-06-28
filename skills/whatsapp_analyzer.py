#!/usr/bin/env python3
"""Skill: whatsapp_analyzer

Analisa conversas WhatsApp armazenadas na tabela interactions.
Gera: sentimento, palavras-chave, tempo de resposta, sugestões de follow-up.
Recebe JSON via stdin, retorna JSON via stdout.

Uso:
    echo '{"lead_id": 1}' | python hermes/skills/whatsapp_analyzer.py
    echo '{"phone": "+5511999998888"}' | python hermes/skills/whatsapp_analyzer.py
"""
import json
import sys
from datetime import datetime, UTC, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection
from app.utils.normalizers import clean_phone


# Palavras-chave de sentimento
POSITIVE = ["interessado", "quero", "gostei", "ok", "beleza", "perfeito", "vamos", "combinado", "ótimo", "excelente"]
NEGATIVE = ["não quero", "pare", "chato", "não tenho interesse", "remova", "cancela", "não", "já tenho", "satisfeito"]
URGENT = ["urgente", "hoje", "agora", "emergência", "preciso", "rápido"]
INTEREST = ["valor", "preço", "quanto custa", "funciona", "como é", "me explica", "pode me ajudar"]


def analyze_interactions(lead_id: int | None = None, phone: str | None = None) -> dict:
    with get_connection() as conn:
        if lead_id:
            rows = conn.execute(
                "SELECT * FROM interactions WHERE lead_id = ? AND channel = 'whatsapp' ORDER BY occurred_at DESC",
                (lead_id,),
            ).fetchall()
            lead = conn.execute("SELECT company_name, whatsapp_number FROM leads WHERE id = ?", (lead_id,)).fetchone()
        elif phone:
            clean = clean_phone(phone)
            lead = conn.execute(
                "SELECT * FROM leads WHERE REPLACE(REPLACE(REPLACE(phone, '(', ''), ')', ''), '-', '') LIKE ? LIMIT 1",
                (f"%{clean}%",),
            ).fetchone()
            if lead:
                lead_id = lead["id"]
                rows = conn.execute(
                    "SELECT * FROM interactions WHERE lead_id = ? AND channel = 'whatsapp' ORDER BY occurred_at DESC",
                    (lead_id,),
                ).fetchall()
            else:
                rows = []
        else:
            rows = []
            lead = None

    interactions = [dict(r) for r in rows]
    if not interactions:
        return {"success": True, "analysis": {}, "logs": ["Nenhuma interação WhatsApp encontrada."]}

    # Sentimento
    pos_count = 0
    neg_count = 0
    urg_count = 0
    int_count = 0
    total_chars = 0
    last_inbound = None
    last_outbound = None
    response_times = []

    for msg in reversed(interactions):  # Ordem cronológica
        text = (msg.get("message_text") or "").lower()
        total_chars += len(text)

        if any(p in text for p in POSITIVE):
            pos_count += 1
        if any(n in text for n in NEGATIVE):
            neg_count += 1
        if any(u in text for u in URGENT):
            urg_count += 1
        if any(i in text for i in INTEREST):
            int_count += 1

        direction = msg.get("direction", "")
        occurred = msg.get("occurred_at")

        if direction == "inbound":
            last_inbound = occurred
            if last_outbound and occurred:
                try:
                    t1 = datetime.fromisoformat(last_outbound.replace("Z", "+00:00"))
                    t2 = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
                    diff = (t2 - t1).total_seconds() / 3600
                    if 0 < diff < 168:  # Até 7 dias
                        response_times.append(diff)
                except Exception:
                    pass
        elif direction == "outbound":
            last_outbound = occurred

    total = len(interactions)
    avg_response = sum(response_times) / len(response_times) if response_times else None

    # Sentimento geral
    if neg_count > pos_count:
        sentiment = "negativo" if neg_count > total * 0.3 else "neutro-negativo"
    elif pos_count > neg_count:
        sentiment = "positivo" if pos_count > total * 0.2 else "neutro-positivo"
    else:
        sentiment = "neutro"

    # Follow-up suggestion
    last_msg = interactions[0] if interactions else {}
    last_date = last_msg.get("occurred_at", "")
    days_since = None
    if last_date:
        try:
            d = datetime.fromisoformat(last_date.replace("Z", "+00:00"))
            days_since = (datetime.now(UTC) - d).days
        except Exception:
            pass

    follow_up = None
    if days_since is not None and days_since > 3:
        follow_up = f"Último contato há {days_since} dias. Sugestão: enviar follow-up perguntando sobre interesse."
    elif sentiment == "positivo" and int_count > 0:
        follow_up = "Sentimento positivo com sinais de interesse. Sugestão: agendar reunião ou enviar proposta."
    elif sentiment == "negativo":
        follow_up = "Sentimento negativo detectado. Sugestão: pausar abordagem por 7-10 dias ou mudar ângulo."
    elif urg_count > 0:
        follow_up = "Urgência detectada. Sugestão: priorizar resposta rápida (menos de 2h)."
    else:
        follow_up = "Conversa em andamento. Manter ritmo de contato a cada 2-3 dias."

    analysis = {
        "lead_id": lead_id,
        "company_name": lead["company_name"] if lead else "N/A",
        "total_messages": total,
        "sentiment": sentiment,
        "positive_signals": pos_count,
        "negative_signals": neg_count,
        "urgency_signals": urg_count,
        "interest_signals": int_count,
        "avg_response_time_hours": round(avg_response, 1) if avg_response else None,
        "last_contact_days_ago": days_since,
        "follow_up_suggestion": follow_up,
    }

    return {"success": True, "analysis": analysis, "logs": [f"Analisadas {total} mensagens de WhatsApp."]}


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "errors": ["JSON inválido"]}))
        sys.exit(1)

    result = analyze_interactions(
        lead_id=payload.get("lead_id"),
        phone=payload.get("phone"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
