"""Hermes Telegram Bot — Interface privada definitiva.

Suporta: texto, audio (voz), foto (OCR), video (transcricao), documento.
Memoria pessoal local persistente (JSON).

Como usar:
1. Crie bot no @BotFather, pegue token
2. Pegue seu chat_id no @userinfobot
3. Adicione no .env: TELEGRAM_BOT_TOKEN e TELEGRAM_OWNER_ID
4. Rode: python hermes/telegram_bot/bot.py
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_env

load_env()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "")

if not TELEGRAM_BOT_TOKEN:
    print("[ERRO] TELEGRAM_BOT_TOKEN nao configurado no .env")
    sys.exit(1)

try:
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
except ImportError:
    print("[ERRO] python-telegram-bot nao instalado. pip install python-telegram-bot")
    sys.exit(1)

from hermes.secretary.core_v2 import HermesCore
from hermes.skills.audio_transcribe import transcribe_audio, is_backend_available
from hermes.secretary.tools.tool_run_skill import run_skill

core = HermesCore()

# --- STT Status ---
_stt_error: str | None = None


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_OWNER_ID):
        await update.message.reply_text("Nao autorizado.")
        return
    await update.message.reply_text(
        "Hermes Secretary ativo!\n\n"
        "Suporto: texto, audio, foto (OCR + Vision), video, documento.\n"
        "Comandos: /help /reset /memory /snapshot"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_OWNER_ID):
        return
    await update.message.reply_text(
        "Comandos:\n"
        "  /start — inicia\n"
        "  /help — ajuda\n"
        "  /reset — limpa historico\n"
        "  /memory — memoria pessoal\n"
        "  /snapshot — status comercial\n\n"
        "Midia suportada:\n"
        "  [foto] — OCR + Vision (cartao, tabela, grafico, print)\n"
        "  [audio/voz] — transcricao + resposta\n"
        "  [video] — extrai audio + transcricao (video skill em breve)\n"
        "  [documento] — le conteudo e processa\n\n"
        "Exemplos:\n"
        "  'Quantos leads?'\n"
        "  'Digest de hoje'\n"
        "  'Briefing do cliente X'\n"
        "  'Sugestao de follow-up'"
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_OWNER_ID):
        return
    core.history.clear()
    await update.message.reply_text("Historico limpo.")


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from hermes.secretary.context.personal_memory import PersonalMemory
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_OWNER_ID):
        return
    mem = PersonalMemory()
    facts = mem.list_facts()
    if not facts:
        await update.message.reply_text("Memoria vazia. Me ensine algo!")
        return
    lines = ["Memoria pessoal:"]
    for f in facts[-15:]:
        lines.append(f"  [{f['category']}] {f['key']}: {f['value']}")
    await update.message.reply_text("\n".join(lines))


async def snapshot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_OWNER_ID):
        return
    snap = core._get_system_snapshot_text()
    await update.message.reply_text(snap or "Sem dados no sistema.")


# --- Mensagens de texto ---

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_OWNER_ID):
        await update.message.reply_text("Nao autorizado.")
        return

    # Intercepta respostas pendentes do fluxo de vídeo
    if await _handle_video_classification(update, context):
        return
    if await _maybe_save_video_summary(update, context):
        return

    user_text = update.message.text or ""

    # ── Comandos de edição de vídeo por caminho local ──
    render_match = re.search(
        r"(?:cortar\s+v[ií]deo|corte\s+do\s+v[ií]deo)\s+(.+?)\s+(?:de|do|in[ií]cio)\s+(.+?)\s+(?:at[ée]|a|at[eé]\s+o|fim)\s+(.+?)(?:\s+(?:vertical|square|quadrado|original))?$",
        user_text,
        re.IGNORECASE,
    )
    if render_match:
        video_path = render_match.group(1).strip().strip('"').strip("'")
        start_ts = render_match.group(2).strip()
        end_ts = render_match.group(3).strip()
        aspect = _aspect_from_text(user_text) or "original"
        if Path(video_path).exists():
            await _run_video_render(
                update,
                context,
                video_path=video_path,
                action="render_cuts",
                cuts=[{
                    "inicio": start_ts,
                    "fim": end_ts,
                    "titulo": "Corte manual",
                    "hook": "",
                    "cta": "",
                }],
                aspect=aspect,
            )
            return
        else:
            await update.message.reply_text(f"Arquivo não encontrado: {video_path}")
            return

    gen_cuts_match = re.search(
        r"(?:gerar\s+cortes|renderizar\s+cortes|fazer\s+cortes)\s+(?:do\s+)?v[ií]deo\s+(.+?)(?:\s+(\d+))?(?:\s+(?:vertical|square|quadrado|original))?$",
        user_text,
        re.IGNORECASE,
    )
    if gen_cuts_match:
        video_path = gen_cuts_match.group(1).strip().strip('"').strip("'")
        n_cuts = int(gen_cuts_match.group(2)) if gen_cuts_match.group(2) else 3
        aspect = _aspect_from_text(user_text) or "original"
        if Path(video_path).exists():
            await _run_video_render(
                update,
                context,
                video_path=video_path,
                action="generate_and_render_cuts",
                n_cuts=n_cuts,
                aspect=aspect,
            )
            return
        else:
            await update.message.reply_text(f"Arquivo não encontrado: {video_path}")
            return

    # Permite processar vídeo local por caminho (ex: vídeos grandes)
    local_video_match = re.search(
        r"(?:processar\s+v[ií]deo|analisar\s+v[ií]deo|resumir\s+v[ií]deo)\s+(.+?)(?:\s+como\s+(lead|escrit[óo]rio|marketing|outro))?$",
        user_text,
        re.IGNORECASE,
    )
    if local_video_match:
        video_path = local_video_match.group(1).strip().strip('"').strip("'")
        context_hint = local_video_match.group(2) or "auto"
        if context_hint.lower() in ("escritorio", "escritório"):
            context_hint = "escritorio"
        if Path(video_path).exists():
            await _run_video_skill(
                update,
                context,
                video_path=video_path,
                caption=f"processado por caminho local: {video_path}",
                context_hint=context_hint,
            )
            return
        else:
            await update.message.reply_text(f"Arquivo não encontrado: {video_path}")
            return

    thinking = await update.message.reply_text("Pensando...")

    try:
        result = core.process_message(user_text, source="telegram_bot")
        await thinking.delete()
        await update.message.reply_text(result.get("response", "Nao entendi."))
    except Exception as exc:
        await thinking.delete()
        await update.message.reply_text(f"Erro: {exc}")


# --- Audio / Voz ---

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_OWNER_ID):
        await update.message.reply_text("Nao autorizado.")
        return

    if _stt_error:
        await update.message.reply_text(f"🎙️ Transcrição de áudio indisponível no momento: {_stt_error[:200]}")
        return

    thinking = await update.message.reply_text("Baixando audio...")
    ogg_path = None
    wav_path = None

    try:
        # 1. Baixa arquivo com timeout generoso
        print("[VOICE] Baixando arquivo...")
        voice_file = await update.message.voice.get_file()
        ogg_path = tempfile.mktemp(suffix=".ogg")
        await voice_file.download_to_drive(ogg_path)
        print(f"[VOICE] Baixado: {ogg_path}")

        await thinking.edit_text("Convertendo formato...")

        # 2. Converte OGG -> WAV
        print("[VOICE] Convertendo OGG -> WAV...")
        wav_path = ogg_path.replace(".ogg", ".wav")
        from pydub import AudioSegment
        audio = AudioSegment.from_file(ogg_path, format="ogg")
        audio.export(wav_path, format="wav")
        print(f"[VOICE] Convertido: {wav_path}")

        await thinking.edit_text("Transcrevendo...")

        # 3. Transcreve com a melhor estratégia: curto -> Whisper local, longo -> Gladia
        print("[VOICE] Transcrevendo...")
        stt_result = transcribe_audio(wav_path)
        transcript = stt_result.get("transcript", "") if stt_result.get("success") else ""
        if not transcript:
            error = stt_result.get("logs", ["Não entendi o áudio. Tente falar mais claro."])[-1]
            await thinking.edit_text(error)
            return
        print(f"[VOICE] Transcricao: {transcript}")

        await thinking.edit_text(f"Transcricao: '{transcript}'\nProcessando...")

        # 4. Envia para o core
        result = core.process_message(transcript, source="telegram_bot")

        await thinking.delete()
        await update.message.reply_text(f"[Audio] {transcript}\n\n{result.get('response', '')}")

    except Exception as exc:
        print(f"[VOICE] ERRO: {exc}")
        await thinking.delete()
        await update.message.reply_text(f"Erro no audio: {exc}")

    finally:
        for p in [ogg_path, wav_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


# --- Foto (OCR + Vision) ---

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_OWNER_ID):
        await update.message.reply_text("Nao autorizado.")
        return

    thinking = await update.message.reply_text("Analisando foto...")
    img_path = None

    try:
        # Pega a foto em maior resolucao disponivel
        photo = update.message.photo[-1] if update.message.photo else None
        if not photo:
            await thinking.edit_text("Foto nao recebida.")
            return

        photo_file = await photo.get_file()
        img_path = tempfile.mktemp(suffix=".jpg")
        await photo_file.download_to_drive(img_path)

        caption = update.message.caption or ""

        # Usa skill vision
        result = run_skill("vision", {
            "image_path": img_path,
            "caption": caption,
            "task": "auto",
        })

        await thinking.delete()

        if not result.get("success"):
            await update.message.reply_text(f"Erro na análise: {result.get('error', 'desconhecido')}")
            return

        output = result.get("output", "Análise concluída.")
        await update.message.reply_text(output)

    except Exception as exc:
        await thinking.delete()
        await update.message.reply_text(f"Erro na foto: {exc}")

    finally:
        if img_path and os.path.exists(img_path):
            try:
                os.remove(img_path)
            except Exception:
                pass


# --- Video ---

_PENDING_VIDEO_KEY = "pending_video"


def _get_pending(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> dict | None:
    return context.bot_data.get(_PENDING_VIDEO_KEY, {}).get(str(chat_id))


def _set_pending(context: ContextTypes.DEFAULT_TYPE, chat_id: int, data: dict | None) -> None:
    if _PENDING_VIDEO_KEY not in context.bot_data:
        context.bot_data[_PENDING_VIDEO_KEY] = {}
    if data is None:
        context.bot_data[_PENDING_VIDEO_KEY].pop(str(chat_id), None)
    else:
        context.bot_data[_PENDING_VIDEO_KEY][str(chat_id)] = data


def _format_minute(minute: dict) -> str:
    """Formata a minuta retornada pela skill video para Telegram."""
    lines = []

    title = minute.get("titulo") or "Minuta de vídeo"
    lines.append(f"🎬 *{title}*")

    context_type = minute.get("tipo") or "geral"
    lines.append(f"📁 Tipo: {context_type}")

    if minute.get("participantes"):
        parts = ", ".join(str(p) for p in minute["participantes"])
        lines.append(f"👥 Participantes: {parts}")

    if minute.get("resumo_executivo"):
        lines.append(f"\n📝 *Resumo executivo*\n{minute['resumo_executivo']}")

    if minute.get("temas_discutidos"):
        lines.append("\n📌 *Temas discutidos*")
        for tema in minute["temas_discutidos"]:
            lines.append(f"• {tema}")

    if minute.get("decisoes"):
        lines.append("\n✅ *Decisões*")
        for decisao in minute["decisoes"]:
            lines.append(f"• {decisao}")

    if minute.get("action_items"):
        lines.append("\n🎯 *Action items*")
        for item in minute["action_items"]:
            quem = item.get("quem") or "Não definido"
            o_que = item.get("o_que") or "—"
            ate = item.get("ate_quando") or "—"
            lines.append(f"• {quem}: {o_que} (até {ate})")

    if minute.get("proximos_passos"):
        lines.append("\n➡️ *Próximos passos*")
        for passo in minute["proximos_passos"]:
            lines.append(f"• {passo}")

    if minute.get("oportunidades_negocio"):
        lines.append("\n💰 *Oportunidades de negócio*")
        for opp in minute["oportunidades_negocio"]:
            lines.append(f"• {opp}")

    if minute.get("riscos_regulatórios"):
        lines.append("\n⚠️ *Riscos regulatórios*")
        for risco in minute["riscos_regulatórios"]:
            lines.append(f"• {risco}")

    if minute.get("duvidas_pendentes"):
        lines.append("\n❓ *Dúvidas pendentes*")
        for duvida in minute["duvidas_pendentes"]:
            lines.append(f"• {duvida}")

    if minute.get("timestamps_relevantes"):
        lines.append("\n⏱️ *Trechos relevantes*")
        for ts in minute["timestamps_relevantes"]:
            inicio = ts.get("timestamp_inicio") or "?"
            fim = ts.get("timestamp_fim") or "?"
            desc = ts.get("descricao") or ""
            lines.append(f"• [{inicio} - {fim}] {desc}")

    return "\n".join(lines)


async def _send_long_message(update: Update, text: str, max_len: int = 3800) -> None:
    """Envia mensagens longas quebrando em pedaços respeitando parágrafos."""
    if len(text) <= max_len:
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    chunks = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 > max_len:
            if current:
                chunks.append(current.strip())
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current.strip())

    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="Markdown")


def _persist_video_file(temp_path: str, chat_id: int) -> str:
    """Copia vídeo temporário para local persistente até decisão de salvamento."""
    runtime_dir = Path.home() / "AppData" / "Local" / "lead_prospecting_engine"
    upload_dir = runtime_dir / "video_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(temp_path).suffix or ".mp4"
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    persistent = upload_dir / f"video_{chat_id}_{ts}{ext}"
    shutil.copy2(temp_path, persistent)
    return str(persistent)


def _cleanup_persistent_video(video_path: str) -> None:
    try:
        if video_path and os.path.exists(video_path):
            os.remove(video_path)
    except Exception:
        pass


def _aspect_from_text(text: str) -> str | None:
    t = (text or "").lower()
    if "vertical" in t or "9:16" in t or "reels" in t or "shorts" in t or "tiktok" in t:
        return "vertical"
    if "square" in t or "quadrado" in t or "1:1" in t:
        return "square"
    if "original" in t:
        return "original"
    return None


def _detect_video_action_from_caption(caption: str) -> tuple[str, dict]:
    """Detecta se a legenda pede resumo (padrão) ou geração/renderização de cortes."""
    t = (caption or "").lower()
    aspect = _aspect_from_text(t) or "original"

    # Cortar trecho específico via legenda: "cortar de 0:12 a 0:52"
    cut_match = re.search(
        r"(?:cortar|corte)\s+(?:de|do|in[ií]cio)?\s*(\d+:\d+(?::\d+)?)\s+(?:at[ée]|a|at[eé]\s+o|fim)\s+(\d+:\d+(?::\d+)?)",
        t,
    )
    if cut_match:
        return "render_cuts", {
            "aspect": aspect,
            "cuts": [{
                "inicio": cut_match.group(1),
                "fim": cut_match.group(2),
                "titulo": "Corte da legenda",
                "hook": "",
                "cta": "",
            }],
        }

    # Gerar/renderizar cortes
    if re.search(r"(?:gerar|fazer|renderizar)\s+cortes", t):
        n_match = re.search(r"\b(\d+)\s+cortes?\b", t)
        n_cuts = int(n_match.group(1)) if n_match else 3
        return "generate_and_render_cuts", {"aspect": aspect, "n_cuts": n_cuts}

    return "summarize", {"aspect": aspect}


async def _run_video_render(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    video_path: str,
    action: str,
    cuts: list[dict] | None = None,
    n_cuts: int = 3,
    aspect: str = "original",
) -> None:
    """Roda skill video em modo renderização e envia os MP4s de volta."""
    thinking = await update.message.reply_text("🎬 Renderizando cortes de vídeo...")

    try:
        payload = {
            "video_path": video_path,
            "action": action,
            "aspect": aspect,
        }
        if action == "render_cuts" and cuts:
            payload["cuts"] = cuts
        if action == "generate_and_render_cuts":
            payload["n_cuts"] = n_cuts

        result = run_skill("video", payload, timeout=600)
        await thinking.delete()

        if not result.get("success"):
            await update.message.reply_text(
                f"Erro ao renderizar cortes: {result.get('error', 'erro desconhecido')}"
            )
            return

        raw = result.get("raw", result)
        rendered = raw.get("rendered_cuts") or raw.get("cuts") or []
        if not rendered:
            await update.message.reply_text("Nenhum corte foi gerado.")
            return

        # Envia texto resumo
        output_text = result.get("output", raw.get("message", "Cortes renderizados."))
        await _send_long_message(update, output_text)

        # Envia cada MP4 renderizado com sucesso
        sent = 0
        for r in rendered:
            if not r.get("success"):
                continue
            path = r.get("output_path")
            if not path or not os.path.exists(path):
                continue
            cut = r.get("cut") or r
            caption = f"{cut.get('titulo', 'Corte')} [{cut.get('inicio', '?')} - {cut.get('fim', '?')}]"
            try:
                with open(path, "rb") as f:
                    await update.message.reply_video(
                        video=f,
                        caption=caption[:1024],
                        supports_streaming=True,
                    )
                sent += 1
            except Exception as exc:
                await update.message.reply_text(f"Erro ao enviar corte {r.get('index', '?')}: {exc}")

        if sent == 0:
            await update.message.reply_text("Nenhum corte foi enviado (verifique o FFmpeg e os caminhos).")

    except Exception as exc:
        await thinking.delete()
        await update.message.reply_text(f"Erro ao renderizar: {exc}")


async def _ask_save_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, payload: dict) -> None:
    """Pergunta se o usuário quer salvar a minuta no CRM."""
    context_type = payload.get("context_type", "outro")
    _set_pending(
        context,
        update.effective_chat.id,
        {
            "mode": "awaiting_save_decision",
            **payload,
        },
    )
    await update.message.reply_text(
        f"Quer salvar isso no CRM? Responda: salvar {context_type} / nao"
    )


async def _maybe_save_video_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Processa resposta de salvamento de minuta de vídeo."""
    chat_id = update.effective_chat.id
    pending = _get_pending(context, chat_id)
    if not pending or pending.get("mode") != "awaiting_save_decision":
        return False

    text = (update.message.text or "").strip().lower()
    context_type = pending.get("context_type", "outro")

    if text in ("nao", "não", "n", "no"):
        _cleanup_persistent_video(pending.get("video_path", ""))
        _set_pending(context, chat_id, None)
        await update.message.reply_text("Ok, não salvei no CRM.")
        return True

    if text.startswith("salvar") or text == context_type:
        _set_pending(context, chat_id, None)
        try:
            from hermes.secretary.tools.tool_save_video_summary import save_video_summary
        except ImportError:
            save_video_summary = None

        if save_video_summary is None:
            await update.message.reply_text(
                "Tool de salvamento ainda não está disponível. A minuta foi processada mas não salva."
            )
            return True

        kwargs = {
            "context_type": pending.get("context_type"),
            "minute": pending.get("minute"),
            "transcript": pending.get("transcript"),
            "caption": pending.get("caption"),
            "video_path": pending.get("video_path", ""),
        }
        if context_type == "lead":
            kwargs["lead_id"] = pending.get("lead_id")

        try:
            result = save_video_summary(**kwargs)
            if result and result.get("success"):
                await update.message.reply_text("✅ Minuta salva no CRM.")
                _cleanup_persistent_video(pending.get("video_path", ""))
            else:
                await update.message.reply_text(
                    f"Erro ao salvar: {result.get('error', 'erro desconhecido')}"
                )
        except Exception as exc:
            await update.message.reply_text(f"Erro ao salvar no CRM: {exc}")
        return True

    return False


async def _handle_video_classification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Processa resposta de classificação de contexto de vídeo."""
    chat_id = update.effective_chat.id
    pending = _get_pending(context, chat_id)
    if not pending or pending.get("mode") != "awaiting_classification":
        return False

    text = (update.message.text or "").strip().lower()
    valid = {"lead", "escritorio", "escritório", "marketing", "outro"}
    if text not in valid:
        return False

    chosen = "escritorio" if text in ("escritorio", "escritório") else text
    _set_pending(context, chat_id, None)
    persistent_path = pending.get("video_path", "")

    # Reenvia o mesmo vídeo para a skill com o contexto escolhido
    await update.message.reply_text(f"Ok, processando como '{chosen}'...")
    await _run_video_skill(
        update,
        context,
        video_path=persistent_path,
        caption=pending["caption"],
        context_hint=chosen,
    )
    # Limpa arquivo persistente após reprocessamento
    _cleanup_persistent_video(persistent_path)
    return True


async def _run_video_skill(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    video_path: str,
    caption: str,
    context_hint: str = "auto",
) -> None:
    """Chama a skill video e trata o resultado (classificação ou minuta)."""
    thinking = await update.message.reply_text("Processando vídeo pela skill...")

    try:
        result = run_skill(
            "video",
            {
                "video_path": video_path,
                "action": "summarize",
                "caption": caption,
                "context_hint": context_hint,
                "extract_slides": True,
            },
            timeout=300,
        )

        await thinking.delete()

        if not result.get("success"):
            await update.message.reply_text(
                f"Erro ao processar vídeo: {result.get('error', 'erro desconhecido')}"
            )
            return

        raw = result.get("raw", result)

        # Skill pede classificação
        if raw.get("ask_classification"):
            suggested = raw.get("suggested_context", {})
            suggested_type = suggested.get("context_type", "escritorio")
            confidence = suggested.get("confidence", 0.0)
            motivo = suggested.get("motivo", "")
            _set_pending(
                context,
                update.effective_chat.id,
                {
                    "mode": "awaiting_classification",
                    "video_path": video_path,
                    "caption": caption,
                },
            )
            msg = (
                f"🎬 Vídeo processado. Parece ser *{suggested_type}*"
                f" (confiança {confidence:.0%}).\n{motivo}\n\n"
                f"Confirma: lead / escritorio / marketing / outro?"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

        # Minuta pronta
        minute = raw.get("minute", {})
        context_type = raw.get("context_type") or minute.get("tipo") or "outro"
        formatted = _format_minute(minute) if minute else "🎬 Vídeo processado, mas sem minuta."

        await _send_long_message(update, formatted)

        # Pergunta se salva no CRM
        await _ask_save_summary(
            update,
            context,
            {
                "context_type": context_type,
                "minute": minute,
                "transcript": raw.get("transcript"),
                "caption": caption,
                "video_path": video_path,
                "lead_id": None,
            },
        )

    except Exception as exc:
        await thinking.delete()
        await update.message.reply_text(f"Erro no vídeo: {exc}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_OWNER_ID):
        await update.message.reply_text("Nao autorizado.")
        return

    if _stt_error:
        await update.message.reply_text(f"🎥 Transcrição de vídeo indisponível no momento: {_stt_error[:200]}")
        return

    thinking = await update.message.reply_text("Baixando vídeo...")
    vid_path = None
    temp_path = None

    try:
        video = update.message.video or update.message.document
        if not video:
            await thinking.edit_text("Video nao recebido.")
            return

        vid_file = await video.get_file()
        ext = ".mp4"
        if update.message.document and update.message.document.file_name:
            ext = Path(update.message.document.file_name).suffix or ".mp4"

        # Limites do Telegram Bot API: download direto até ~20 MB
        file_size = getattr(vid_file, "file_size", None) or getattr(video, "file_size", 0)
        if file_size and file_size > 19_500_000:
            await thinking.edit_text(
                "🎥 Vídeo muito grande para o Telegram (limite ~20 MB).\n\n"
                "Opções:\n"
                "1) Comprima o vídeo antes de enviar;\n"
                "2) Envie como arquivo .zip (será tratado como documento);\n"
                "3) Coloque o vídeo em uma pasta local e me envie o caminho completo, ex:\n"
                "   processar video C:\\Users\\Juan\\Videos\\reuniao.mp4 como escritorio"
            )
            return

        temp_path = tempfile.mktemp(suffix=ext)
        await vid_file.download_to_drive(temp_path)
        await thinking.delete()

        # Persiste o vídeo para permitir reprocessamento (classificação/salvamento)
        vid_path = _persist_video_file(temp_path, chat_id)
        caption = update.message.caption or ""

        action, action_kwargs = _detect_video_action_from_caption(caption)
        if action in ("render_cuts", "generate_and_render_cuts"):
            await _run_video_render(
                update,
                context,
                video_path=vid_path,
                action=action,
                **action_kwargs,
            )
        else:
            await _run_video_skill(
                update,
                context,
                video_path=vid_path,
                caption=caption,
                context_hint="auto",
            )

    except Exception as exc:
        await thinking.delete()
        error_text = str(exc)
        if "too big" in error_text.lower() or "too_large" in error_text.lower():
            await update.message.reply_text(
                "🎥 Vídeo muito grande para o Telegram (limite ~20 MB).\n\n"
                "Opções:\n"
                "1) Comprima o vídeo antes de enviar;\n"
                "2) Envie como arquivo .zip (será tratado como documento);\n"
                "3) Coloque o vídeo em uma pasta local e me envie o caminho completo, ex:\n"
                "   processar video C:\\Users\\Juan\\Videos\\reuniao.mp4 como escritorio"
            )
        else:
            await update.message.reply_text(f"Erro no video: {exc}")

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        # O arquivo persistente é limpo após salvamento ou reprocessamento;
        # se o usuário não responder, ficará em video_uploads para limpeza futura.


# --- Documento ---

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if str(chat_id) != str(TELEGRAM_OWNER_ID):
        await update.message.reply_text("Nao autorizado.")
        return

    thinking = await update.message.reply_text("Lendo documento...")
    doc_path = None

    try:
        doc = update.message.document
        if not doc:
            await thinking.edit_text("Documento nao recebido.")
            return

        doc_file = await doc.get_file()
        ext = Path(doc.file_name or "doc.bin").suffix
        doc_path = tempfile.mktemp(suffix=ext)
        await doc_file.download_to_drive(doc_path)

        # Le conteudo
        content = ""
        if ext.lower() in [".txt", ".md", ".csv", ".json", ".py", ".js", ".html"]:
            try:
                content = Path(doc_path).read_text(encoding="utf-8")
            except Exception:
                content = "[Erro ao ler como texto]"
        else:
            content = f"[Arquivo binario: {doc.file_name}, tamanho: {doc.file_size} bytes]"

        caption = update.message.caption or ""
        prompt = f"Usuario enviou um documento: {doc.file_name}\nConteudo:\n{content[:3000]}\n\nLegenda: {caption}\nResponda de forma util."
        result = core.process_message(prompt, source="telegram_bot")

        await thinking.delete()
        await update.message.reply_text(f"[Doc: {doc.file_name}]\n{result.get('response', '')}")

    except Exception as exc:
        await thinking.delete()
        await update.message.reply_text(f"Erro no documento: {exc}")

    finally:
        if doc_path and os.path.exists(doc_path):
            try:
                os.remove(doc_path)
            except Exception:
                pass


# --- Entry point ---

def main() -> None:
    print("[BOT] Iniciando Hermes Telegram Bot...")
    print(f"   Token: {'OK' if TELEGRAM_BOT_TOKEN else 'FALTANDO'}")
    print(f"   Owner ID: {TELEGRAM_OWNER_ID or 'FALTANDO'}")
    print("   Recursos: texto, audio (Gladia/Whisper), foto, video, documento")
    print("   Memoria local: ativa")
    print(f"   STT: Gladia {'ATIVO' if is_backend_available('gladia') else 'N/A (fallback Whisper local)'}")
    print("   Pronto. Pressione Ctrl+C para parar.\n")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .get_updates_read_timeout(60)
        .build()
    )

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("snapshot", snapshot_command))

    # Mensagens
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
