"""Tool: run_skill — Executa skills existentes do Hermes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SKILL_DIR = ROOT / "hermes" / "skills"


def _format_skill_output(skill_name: str, result: dict) -> str | None:
    """Formata output específico de skills para Telegram."""
    if skill_name == "lead_finder" and result.get("success"):
        leads = result.get("leads", [])
        lines = [result.get("message", "")]
        if leads:
            lines.append("")
            for i, lead in enumerate(leads[:10], 1):
                phone = lead.get("phone") or lead.get("whatsapp_number") or "sem contato"
                lines.append(
                    f"{i}. {lead.get('company_name', 'Sem nome')} — "
                    f"⭐ {lead.get('score_total', 0)} | "
                    f"📞 {phone} | "
                    f"🌡️ {lead.get('temperature', 'cold')}"
                )
        if result.get("auto_save", False):
            lines.append("\n✅ Leads salvos no CRM.")
        else:
            lines.append("\n💾 Responda 'salvar' para guardar esses leads no CRM.")
        return "\n".join(lines)

    if skill_name == "vision" and result.get("success"):
        lines = ["🖼️ Análise da imagem:"]
        if result.get("ocr_text"):
            lines.append(f"📝 Texto detectado:\n{result['ocr_text'][:500]}")
        if result.get("analysis"):
            lines.append(f"\n💡 Interpretação:\n{result['analysis']}")
        if result.get("suggested_action"):
            lines.append(f"\n➡️ Próximo passo: {result['suggested_action']}")
        return "\n".join(lines)

    if skill_name == "video" and result.get("success"):
        # Se ainda precisa de classificação, pergunta ao usuário
        if result.get("ask_classification"):
            suggested = result.get("suggested_context") or {}
            lines = [
                f"🎬 Vídeo processado, mas não consegui classificar com certeza.",
                f"Sugestão: *{suggested.get('context_type', 'outro')}* (confiança: {suggested.get('confidence', 0):.0%})",
                f"Motivo: {suggested.get('motivo', 'nenhum')}",
                "",
                "Qual o tipo deste vídeo? Responda: *lead*, *escritorio*, *marketing* ou *outro*.",
            ]
            return "\n".join(lines)

        minute = result.get("minute") or {}
        lines = [f"🎬 {result.get('message', 'Vídeo processado')}"]
        if minute.get("titulo"):
            lines.append(f"\n*{minute['titulo']}*")

        if minute.get("resumo_executivo"):
            lines.append(f"\n📝 Resumo:\n{minute['resumo_executivo']}")

        if minute.get("decisoes"):
            lines.append("\n✅ Decisões:")
            for d in minute["decisoes"][:5]:
                lines.append(f"- {d}")

        if minute.get("action_items"):
            lines.append("\n🎯 Action items:")
            for item in minute["action_items"][:5]:
                if isinstance(item, dict):
                    quem = item.get("quem", "a definir")
                    o_que = item.get("o_que", "")
                    ate = item.get("ate_quando", "")
                    lines.append(f"- {quem}: {o_que} (até {ate})")
                else:
                    lines.append(f"- {item}")

        if minute.get("proximos_passos"):
            lines.append("\n➡️ Próximos passos:")
            for p in minute["proximos_passos"][:5]:
                lines.append(f"- {p}")

        if minute.get("oportunidades_negocio"):
            lines.append("\n💼 Oportunidades de negócio:")
            for o in minute["oportunidades_negocio"][:5]:
                lines.append(f"- {o}")

        if minute.get("sinais_de_interesse"):
            lines.append("\n🟢 Sinais de interesse:")
            for s in minute["sinais_de_interesse"][:5]:
                lines.append(f"- {s}")

        if result.get("save_candidates"):
            lines.append("\n💾 Posso salvar no CRM:")
            for c in result["save_candidates"]:
                target = c.get("target", "")
                desc = c.get("description", "")
                lines.append(f"- {target}: {desc}")

        # Cortes renderizados
        if result.get("action") in ("render_cuts", "generate_and_render_cuts"):
            lines = [f"🎬 {result.get('message', 'Cortes renderizados')}"]
            rendered = result.get("rendered_cuts") or result.get("cuts") or []
            for r in rendered:
                cut = r.get("cut") or r
                if r.get("success"):
                    path = r.get("output_path", "")
                    titulo = cut.get("titulo", "Corte")
                    inicio = cut.get("inicio", "?")
                    fim = cut.get("fim", "?")
                    lines.append(f"✅ {titulo} [{inicio} - {fim}] → {path}")
                else:
                    err = r.get("error", "erro desconhecido")
                    lines.append(f"❌ Corte {r.get('index', '?')}: {err}")
            if result.get("output_dir"):
                lines.append(f"\n📁 Pasta: {result['output_dir']}")
            return "\n".join(lines)

        return "\n".join(lines)

    return None


def run_skill(skill_name: str, payload: dict, timeout: int | None = None) -> dict:
    """Executa uma skill via subprocess e retorna resultado.

    Args:
        skill_name: nome do arquivo da skill em hermes/skills/ (sem .py).
        payload: dict enviado à skill via stdin como JSON.
        timeout: timeout em segundos (padrão 120).
    """
    skill_path = SKILL_DIR / f"{skill_name}.py"
    if not skill_path.exists():
        return {"success": False, "error": f"Skill '{skill_name}' não encontrada em {skill_path}."}

    try:
        proc = subprocess.run(
            [sys.executable, str(skill_path)],
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=timeout or 120,
            cwd=str(ROOT),
        )
        if proc.returncode != 0:
            return {"success": False, "error": proc.stderr.decode("utf-8", errors="replace")}
        result = json.loads(proc.stdout.decode("utf-8"))
        # Preserva flag auto_save para formatadores
        if skill_name == "lead_finder":
            result["auto_save"] = payload.get("auto_save", False)
        # Formata output para chat
        formatted = _format_skill_output(skill_name, result)

        # Determina se a skill exige aprovação humana por padrão
        approval_required = skill_name in ("lead_finder", "vision", "video")
        if skill_name == "vision" and result.get("task") != "cartao":
            approval_required = False
        if skill_name == "lead_finder" and payload.get("auto_save"):
            approval_required = True  # salvamento ainda exige aprovação

        base_response = {
            "success": True,
            "type": "skill_result",
            "requires_approval": approval_required,
            "draft": result.get("draft"),
            "actions": [],
            "skill_name": skill_name,
            "raw": result,
        }

        if formatted:
            return {**base_response, "output": formatted}
        if "digest" in result:
            return {**base_response, "output": result["digest"]}
        if "briefing" in result:
            return {**base_response, "output": result["briefing"]}
        return {**base_response, "output": json.dumps(result, ensure_ascii=False, indent=2)}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Skill timeout ({timeout or 120}s)"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
