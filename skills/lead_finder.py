#!/usr/bin/env python3
"""Skill: lead_finder

Busca novos leads no Google Places por cidade e segmento.
Aplica scoring heurístico via LeadPipelineService.
Por padrão retorna preview scored sem salvar no CRM.

Uso:
    echo '{"city": "Palmas", "state": "PR", "segment": "clínicas odontológicas"}' | python hermes/skills/lead_finder.py
    echo '{"city": "Palmas", "state": "PR", "segment": "clínicas odontológicas", "auto_save": true}' | python hermes/skills/lead_finder.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Garante UTF-8 no stdout no Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import load_env
from app.integrations.google_places import GooglePlacesClient
from app.integrations.search_presets import SearchPreset
from app.repositories.lead_repository import LeadRepository
from app.services.pipeline import LeadPipelineService

load_env()


def _load_existing_external_ids() -> set[str]:
    """Carrega external_ids já existentes no CRM para evitar duplicados."""
    repo = LeadRepository()
    leads = repo.list_leads(limit=10000)
    return {str(lead.get("external_id") or "").strip() for lead in leads if lead.get("external_id")}


def find_leads(
    city: str,
    state: str,
    segment: str,
    max_results: int = 10,
    auto_save: bool = False,
) -> dict:
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        return {"success": False, "errors": ["GOOGLE_PLACES_API_KEY não configurada no .env"]}

    client = GooglePlacesClient(api_key=api_key)
    pipeline = LeadPipelineService()
    repo = LeadRepository()

    preset = SearchPreset(city=city, state=state, segment=segment, query=f"{segment} em {city} {state}")

    try:
        raw_leads = client.fetch_leads_for_preset(preset, max_results=max_results)
    except Exception as exc:
        return {"success": False, "errors": [f"Erro na busca Google Places: {exc}"]}

    existing_ids = _load_existing_external_ids()

    results: list[dict] = []
    saved_count = 0

    for raw in raw_leads:
        external_id = raw.get("external_id") or ""
        if external_id and str(external_id).strip() in existing_ids:
            continue

        lead = pipeline.build_lead(raw)
        record = lead.to_record()
        record["source_batch"] = f"lead_finder_{city.lower()}_{state.lower()}_{datetime.now(timezone.utc).strftime('%Y%m%d')}"

        lead_summary = {
            "company_name": lead.company_name,
            "city": lead.city,
            "state": lead.state,
            "category": lead.category,
            "phone": lead.phone,
            "whatsapp_number": lead.whatsapp_number,
            "website": lead.website,
            "rating": lead.rating,
            "review_count": lead.review_count,
            "score_total": lead.score_total,
            "lead_grade": lead.lead_grade,
            "temperature": lead.temperature,
            "outreach_status": lead.outreach_status,
            "hot_lead": lead.hot_lead,
            "external_id": lead.external_id,
            "score_breakdown": getattr(lead, "score_breakdown", {}) or {},
            "why_prioritize": getattr(lead, "why_prioritize", "") or "",
            # first_contact_message não é exposto como ação automática — exige aprovação humana
        }

        if auto_save:
            try:
                repo.upsert(record)
                saved_count += 1
                lead_summary["saved"] = True
            except Exception as exc:
                lead_summary["saved"] = False
                lead_summary["save_error"] = str(exc)
        else:
            lead_summary["saved"] = False

        results.append(lead_summary)

    # Ordena por score decrescente
    results.sort(key=lambda x: x.get("score_total", 0), reverse=True)

    message_lines = [f"🔍 {len(results)} empresas encontradas em {city}-{state} para '{segment}'"]
    if auto_save:
        message_lines.append(f"💾 {saved_count} salvas no CRM.")
    else:
        message_lines.append("💾 Preview — nenhuma empresa foi salva ainda. Confirme para salvar.")

    return {
        "success": True,
        "leads": results,
        "saved_count": saved_count,
        "message": "\n".join(message_lines),
    }


def main():
    try:
        if sys.stdin.isatty():
            payload = {}
        else:
            raw = sys.stdin.buffer.read().decode("utf-8")
            payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "errors": ["JSON inválido na entrada"]}))
        sys.exit(1)

    city = payload.get("city", "")
    state = payload.get("state", "")
    segment = payload.get("segment", "")

    if not city or not state or not segment:
        print(json.dumps({"success": False, "errors": ["city, state e segment são obrigatórios"]}))
        sys.exit(1)

    result = find_leads(
        city=city,
        state=state,
        segment=segment,
        max_results=payload.get("max_results", 10),
        auto_save=payload.get("auto_save", False),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
