"""Tool: save_video_summary — Persiste minuta de vídeo/reunião no CRM.

Recebe a minuta estruturada gerada pela skill `video` e salva no SQLite:
- interaction (context_type == lead)
- meetings outcome/next_step (se meeting_id informado)
- opportunities (se houver sinais de negócio)
- hermes_alerts (insights, conhecimento, marketing, notas)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection
from app.repositories.lead_repository import LeadRepository


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def _find_lead_by_text(text: str | None) -> dict | None:
    """Busca lead por nome de empresa, contato ou decisor."""
    if not text or len(text.strip()) < 3:
        return None

    search = text.strip()
    repo = LeadRepository()
    leads = repo.list_leads(search=search, limit=5)
    if leads:
        return leads[0]

    # Fallback: busca por palavras individuais (nome próprio, empresa curta)
    words = [w for w in search.split() if len(w) >= 3]
    for word in words:
        leads = repo.list_leads(search=word, limit=3)
        if leads:
            return leads[0]
    return None


def _lead_has_company_id_column(conn) -> bool:
    try:
        rows = conn.execute("PRAGMA table_info(leads)").fetchall()
        return any(row["name"] == "company_id" for row in rows)
    except Exception:
        return False


def _opportunities_table_exists(conn) -> bool:
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='opportunities'"
        ).fetchall()
        return bool(rows)
    except Exception:
        return False


def _hermes_alerts_table_exists(conn) -> bool:
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hermes_alerts'"
        ).fetchall()
        return bool(rows)
    except Exception:
        return False


def _get_company_id_for_lead(conn, lead_id: int, provided_company_id: int | None) -> int:
    if provided_company_id:
        return provided_company_id

    if _lead_has_company_id_column(conn):
        row = conn.execute(
            "SELECT company_id FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        if row and row["company_id"]:
            return int(row["company_id"])

    # Fallback: tenta encontrar company pelo nome do lead
    lead = LeadRepository().get_lead(lead_id)
    if lead:
        company_name = lead.get("company_name") or lead.get("contact_name")
        if company_name:
            row = conn.execute(
                "SELECT id FROM companies WHERE razao_social LIKE ? OR nome_fantasia LIKE ? LIMIT 1",
                (f"%{company_name}%", f"%{company_name}%"),
            ).fetchone()
            if row:
                return int(row["id"])

    return 0


def _build_summary_text(minute: dict[str, Any]) -> str:
    """Converte minuta em texto legível para interaction."""
    lines: list[str] = []
    title = minute.get("titulo") or "Resumo de reunião gravada"
    lines.append(f"**{title}**")

    if minute.get("resumo_executivo"):
        lines.append(f"\nResumo: {minute['resumo_executivo']}")

    if minute.get("decisoes"):
        lines.append("\nDecisões:")
        for d in minute["decisoes"]:
            lines.append(f"- {d}")

    if minute.get("action_items"):
        lines.append("\nAction items:")
        for item in minute["action_items"]:
            if isinstance(item, dict):
                quem = item.get("quem", "a definir")
                o_que = item.get("o_que", "")
                ate = item.get("ate_quando", "")
                lines.append(f"- {quem}: {o_que} (até {ate})")
            else:
                lines.append(f"- {item}")

    if minute.get("proximos_passos"):
        lines.append("\nPróximos passos:")
        for p in minute["proximos_passos"]:
            lines.append(f"- {p}")

    if minute.get("oportunidades_negocio"):
        lines.append("\nOportunidades de negócio:")
        for o in minute["oportunidades_negocio"]:
            lines.append(f"- {o}")

    if minute.get("sinais_de_interesse"):
        lines.append("\nSinais de interesse:")
        for s in minute["sinais_de_interesse"]:
            lines.append(f"- {s}")

    if minute.get("objecoes"):
        lines.append("\nObjeções:")
        for o in minute["objecoes"]:
            lines.append(f"- {o}")

    if minute.get("riscos_regulatórios"):
        lines.append("\nRiscos regulatórios:")
        for r in minute["riscos_regulatórios"]:
            lines.append(f"- {r}")

    return "\n".join(lines)


def _create_alert(
    conn,
    alert_type: str,
    title: str,
    description: str,
    lead_id: int | None = None,
    company_id: int | None = None,
    suggested_action: str | None = None,
) -> int:
    now = _now_utc()
    cursor = conn.execute(
        """
        INSERT INTO hermes_alerts (alert_type, lead_id, company_id, title, description, suggested_action, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (alert_type, lead_id, company_id, title, description, suggested_action, "novo", now),
    )
    return int(cursor.lastrowid)


def _create_opportunity_if_needed(
    conn,
    lead_id: int,
    company_id: int,
    minute: dict[str, Any],
) -> int | None:
    oportunidades = minute.get("oportunidades_negocio") or []
    sinais = minute.get("sinais_de_interesse") or []
    if not oportunidades and not sinais:
        return None

    # Verifica se já existe oportunidade aberta para o lead
    existing = conn.execute(
        "SELECT id FROM opportunities WHERE lead_id = ? AND status = 'aberta' LIMIT 1",
        (lead_id,),
    ).fetchone()
    if existing:
        return int(existing["id"])

    title = (
        minute.get("titulo")
        or (oportunidades[0] if oportunidades else "Interesse detectado em reunião")
    )
    now = _now_utc()
    cursor = conn.execute(
        """
        INSERT INTO opportunities (lead_id, company_id, title, stage, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (lead_id, company_id, title, "prospeccao", "aberta", now, now),
    )
    return int(cursor.lastrowid)


def _extract_next_action(minute: dict[str, Any]) -> str:
    proximos = minute.get("proximos_passos") or []
    actions = minute.get("action_items") or []
    parts: list[str] = []
    if proximos:
        parts.append("; ".join(str(p) for p in proximos[:3]))
    if actions:
        for item in actions[:3]:
            if isinstance(item, dict):
                parts.append(f"{item.get('quem', 'Juan')}: {item.get('o_que', '')}")
            else:
                parts.append(str(item))
    return " | ".join(parts) if parts else "Acompanhar próximos passos da minuta"


def save_video_summary(
    context_type: str,
    minute: dict[str, Any],
    transcript: dict[str, Any] | None,
    video_path: str,
    caption: str = "",
    lead_id: int | None = None,
    company_id: int | None = None,
    meeting_id: int | None = None,
) -> dict:
    """Persiste minuta de vídeo/reunião no banco e gera alertas."""
    if not minute or not isinstance(minute, dict):
        return {"success": False, "error": "Minuta inválida ou vazia."}

    context_type = (context_type or "outro").lower()
    if context_type not in {"lead", "escritorio", "marketing", "outro"}:
        return {
            "success": False,
            "error": f"context_type inválido: {context_type}. Use lead, escritorio, marketing ou outro.",
        }

    created_ids: dict[str, Any] = {
        "interaction_id": None,
        "alert_ids": [],
        "opportunity_id": None,
        "meeting_updated": False,
    }

    try:
        with get_connection() as conn:
            # ── LEAD ─────────────────────────────────────────────────────
            if context_type == "lead":
                if not lead_id:
                    search_text = minute.get("titulo") or caption
                    lead_match = _find_lead_by_text(search_text)
                    if lead_match:
                        lead_id = int(lead_match["id"])
                    else:
                        return {
                            "success": False,
                            "error": "Não encontrei um lead pelo título ou legenda. Informe lead_id.",
                            "needs_lead_id": True,
                        }

                lead = LeadRepository().get_lead(lead_id)
                if not lead:
                    return {
                        "success": False,
                        "error": f"Lead {lead_id} não encontrado.",
                        "needs_lead_id": True,
                    }

                # Registra interaction
                summary_text = _build_summary_text(minute)
                repo = LeadRepository()
                repo.add_interaction(
                    lead_id=lead_id,
                    channel="video",
                    direction="inbound",
                    message_text=summary_text,
                    interaction_type="reuniao_gravada",
                    status="registrado",
                    occurred_at=_now_utc(),
                )
                created_ids["interaction_id"] = True  # add_interaction não retorna id

                # Atualiza meeting se informado
                if meeting_id:
                    repo.update_meeting(
                        meeting_id=meeting_id,
                        payload={
                            "outcome": " | ".join(str(d) for d in (minute.get("decisoes") or []))[:500],
                            "next_step": _extract_next_action(minute)[:500],
                            "notes": summary_text[:2000],
                        },
                    )
                    created_ids["meeting_updated"] = True

                # Cria opportunity se houver sinais de negócio
                if _opportunities_table_exists(conn):
                    resolved_company_id = _get_company_id_for_lead(conn, lead_id, company_id)
                    opp_id = _create_opportunity_if_needed(conn, lead_id, resolved_company_id, minute)
                    if opp_id:
                        created_ids["opportunity_id"] = opp_id

                # Alertas de insight
                if _hermes_alerts_table_exists(conn):
                    oportunidades = minute.get("oportunidades_negocio") or []
                    sinais = minute.get("sinais_de_interesse") or []

                    if oportunidades:
                        alert_id = _create_alert(
                            conn,
                            alert_type="insight",
                            lead_id=lead_id,
                            company_id=company_id,
                            title=f"Oportunidade em reunião: {minute.get('titulo', 'Lead')}",
                            description="\n".join(f"- {o}" for o in oportunidades[:5]),
                            suggested_action="Preparar proposta ou follow-up comercial.",
                        )
                        created_ids["alert_ids"].append(alert_id)

                    if sinais:
                        alert_id = _create_alert(
                            conn,
                            alert_type="insight",
                            lead_id=lead_id,
                            company_id=company_id,
                            title=f"Sinal de interesse: {minute.get('titulo', 'Lead')}",
                            description="\n".join(f"- {s}" for s in sinais[:5]),
                            suggested_action="Avançar com próximo passo da minuta.",
                        )
                        created_ids["alert_ids"].append(alert_id)

                    proximos = minute.get("proximos_passos") or []
                    if proximos:
                        alert_id = _create_alert(
                            conn,
                            alert_type="insight",
                            lead_id=lead_id,
                            company_id=company_id,
                            title=f"Próximos passos: {minute.get('titulo', 'Lead')}",
                            description="\n".join(f"- {p}" for p in proximos[:5]),
                            suggested_action=_extract_next_action(minute),
                        )
                        created_ids["alert_ids"].append(alert_id)

                return {
                    "success": True,
                    "message": f"Resumo de reunião salvo para o lead {lead_id}.",
                    "lead_id": lead_id,
                    **created_ids,
                }

            # ── ESCRITÓRIO ───────────────────────────────────────────────
            if context_type == "escritorio":
                if not _hermes_alerts_table_exists(conn):
                    return {"success": False, "error": "Tabela hermes_alerts não encontrada no banco."}

                title = minute.get("titulo") or "Reunião interna"
                description = minute.get("resumo_executivo") or json.dumps(minute, ensure_ascii=False, default=str)

                alert_id = _create_alert(
                    conn,
                    alert_type="conhecimento",
                    lead_id=lead_id,
                    company_id=company_id,
                    title=f"Conhecimento: {title}",
                    description=description,
                    suggested_action="Consultar quando necessário.",
                )
                created_ids["alert_ids"].append(alert_id)

                oportunidades = minute.get("oportunidades_negocio") or []
                if oportunidades:
                    alert_id = _create_alert(
                        conn,
                        alert_type="oportunidade",
                        lead_id=lead_id,
                        company_id=company_id,
                        title=f"Oportunidade detectada em reunião interna: {title}",
                        description="\n".join(f"- {o}" for o in oportunidades[:5]),
                        suggested_action="Avaliar viabilidade comercial e encaminhar para Juan.",
                    )
                    created_ids["alert_ids"].append(alert_id)

                return {
                    "success": True,
                    "message": f"Nota interna salva: {title}.",
                    **created_ids,
                }

            # ── MARKETING ────────────────────────────────────────────────
            if context_type == "marketing":
                if not _hermes_alerts_table_exists(conn):
                    return {"success": False, "error": "Tabela hermes_alerts não encontrada no banco."}

                title = minute.get("titulo") or "Briefing de marketing"
                parts = []
                for key in ["tema_central", "gancho_principal", "cta_natural", "tom_sugerido", "publico_alvo"]:
                    if minute.get(key):
                        parts.append(f"{key}: {minute[key]}")
                if minute.get("cortes_ideais"):
                    parts.append("Cortes sugeridos:")
                    for c in minute["cortes_ideais"]:
                        if isinstance(c, dict):
                            parts.append(f"- {c.get('inicio')} a {c.get('fim')}: {c.get('hook')} | CTA: {c.get('cta')}")
                        else:
                            parts.append(f"- {c}")
                description = "\n".join(parts) or json.dumps(minute, ensure_ascii=False, default=str)

                alert_id = _create_alert(
                    conn,
                    alert_type="marketing",
                    lead_id=lead_id,
                    company_id=company_id,
                    title=f"Briefing: {title}",
                    description=description,
                    suggested_action="Reusar para criação de conteúdo.",
                )
                created_ids["alert_ids"].append(alert_id)

                return {
                    "success": True,
                    "message": f"Briefing de marketing salvo: {title}.",
                    **created_ids,
                }

            # ── OUTRO ────────────────────────────────────────────────────
            if context_type == "outro":
                if not _hermes_alerts_table_exists(conn):
                    return {"success": False, "error": "Tabela hermes_alerts não encontrada no banco."}

                title = minute.get("titulo") or "Resumo de vídeo"
                description = minute.get("resumo_executivo") or json.dumps(minute, ensure_ascii=False, default=str)

                alert_id = _create_alert(
                    conn,
                    alert_type="nota",
                    lead_id=lead_id,
                    company_id=company_id,
                    title=f"Nota: {title}",
                    description=description,
                    suggested_action="Revisar quando necessário.",
                )
                created_ids["alert_ids"].append(alert_id)

                return {
                    "success": True,
                    "message": f"Nota salva: {title}.",
                    **created_ids,
                }

    except Exception as exc:
        return {"success": False, "error": f"Erro ao salvar resumo: {exc}"}

    return {"success": False, "error": "Comportamento inesperado."}
