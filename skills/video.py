#!/usr/bin/env python3
"""
Skill: video

Processamento real de vídeos e reuniões para o Hermes.
Ações suportadas:
- summarize: gera minuta inteligente estruturada (padrão)
- transcribe: transcrição com diarização e timestamps
- extract_frames: extrai frames-chave
- suggest_cuts: cortes para Shorts/Reels (marketing, texto)
- render_cuts: renderiza cortes pré-definidos em MP4
- generate_and_render_cuts: gera cortes com LLM e renderiza MP4

Tipos de contexto:
- auto: detecta/pergunta classificação
- lead: reunião com lead/cliente
- escritorio: reunião interna do escritório
- marketing: conteúdo de marketing
- outro: genérico

Parâmetros extras para render_cuts / generate_and_render_cuts:
- cuts: lista de dicts com inicio/fim/titulo/hook/cta (render_cuts)
- n_cuts: quantidade de cortes a gerar (generate_and_render_cuts, default 3)
- target_duration: duração alvo como string, ex "30-60s" (generate_and_render_cuts)
- aspect: original | vertical | square (default original)
- output_dir: pasta de saída (default AppData/Local/lead_prospecting_engine/video_cuts)

Requer FFmpeg instalado e disponível no PATH.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, UTC
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

SYSTEM_BASE = (
    "Você é Hermes, secretário operacional de Juan, assessor de investimentos no "
    "escritório 1A Investimentos, credenciado pela XP Investimentos.\n"
    "Analise o conteúdo a seguir e produza um resumo profissional, direto e útil.\n"
    "- NUNCA prometa rentabilidade.\n"
    "- NUNCA dê recomendação de investimento genérica ou personalize sem dados.\n"
    "- Foque em decisões, próximos passos e informações acionáveis.\n"
    "- Respeite o enquadramento regulatório da CVM/XP.\n"
)

PROMPT_CLASSIFY = (
    "Você recebeu a transcrição de um vídeo e uma legenda opcional. "
    "Classifique o vídeo em UMA das categorias abaixo.\n\n"
    "Categorias:\n"
    "- lead: reunião, ligação ou mensagem de vídeo com um lead/cliente prospect.\n"
    "- escritorio: reunião interna do escritório 1A Investimentos/XP (ex: consórcios, processos, produtos).\n"
    "- marketing: conteúdo de marketing/prospecção para redes sociais.\n"
    "- outro: nenhuma das anteriores.\n\n"
    "Transcrição:\n{transcript}\n\n"
    "Legenda: {caption}\n\n"
    "Responda APENAS com um JSON no formato:\n"
    '{"context_type": "lead|escritorio|marketing|outro", "confidence": 0.0-1.0, "motivo": "..."}'
)

PROMPT_OFFICE_MEETING = (
    "Você está resumindo uma REUNIÃO INTERNA do escritório 1A Investimentos/XP. "
    "O foco é capturar conhecimento interno, processos, produtos e próximos passos — "
    "NÃO force abordagem comercial de lead.\n\n"
    "Base:\n"
    "- Transcrição com falantes e timestamps:\n{transcript}\n\n"
    "- Texto extraído de slides/tela (se houver):\n{slides_text}\n\n"
    "Gere uma minuta estruturada em JSON com os campos:\n"
    "tipo: 'escritorio'\n"
    "titulo: título sugerido para a reunião\n"
    "participantes: lista de nomes/falantes identificados (ou 'Falante A', 'Falante B')\n"
    "resumo_executivo: 2-4 frases com o essencial\n"
    "temas_discutidos: lista de tópicos\n"
    "decisoes: decisões tomadas\n"
    "action_items: lista de {{quem, o_que, ate_quando}} — use 'a definir' quando não souber\n"
    "proximos_passos: lista de próximos passos práticos\n"
    "riscos_regulatórios: alertas de compliance/CVM/XP mencionados ou implícitos\n"
    "oportunidades_negocio: oportunidades de negócio identificadas (pode ser vazio)\n"
    "duvidas_pendentes: perguntas que ficaram sem resposta\n"
    "timestamps_relevantes: lista de {{timestamp_inicio, timestamp_fim, descricao}}\n\n"
    "Instruções:\n"
    "- Se a transcrição for sobre consórcios, capture regras, produtos, processos e alinhamentos.\n"
    "- Se houver slides com dados, incorpore-os no resumo.\n"
    "- Timestamps no formato MM:SS ou HH:MM:SS."
)

PROMPT_LEAD_MEETING = (
    "Você está resumindo uma REUNIÃO/LIGAÇÃO com um LEAD ou CLIENTE do Juan. "
    "O foco é capturar sinais comerciais, objeções, decisões e próximos passos.\n\n"
    "Base:\n"
    "- Transcrição com falantes e timestamps:\n{transcript}\n\n"
    "- Texto extraído de slides/tela (se houver):\n{slides_text}\n\n"
    "Gere uma minuta estruturada em JSON com os campos:\n"
    "tipo: 'lead'\n"
    "titulo: título sugerido\n"
    "participantes: lista de nomes/falantes identificados\n"
    "resumo_executivo: 2-4 frases com o essencial comercial\n"
    "temas_discutidos: lista de tópicos\n"
    "decisoes: decisões tomadas pelo lead\n"
    "action_items: lista de {{quem, o_que, ate_quando}} — use 'a definir' quando não souber\n"
    "proximos_passos: lista de próximos passos de follow-up\n"
    "riscos_regulatórios: alertas de compliance/CVM/XP mencionados\n"
    "oportunidades_negocio: produtos/serviços que podem ser oferecidos\n"
    "objecoes: objeções levantadas pelo lead\n"
    "sinais_de_interesse: frases que indicam interesse real\n"
    "duvidas_pendentes: perguntas sem resposta\n"
    "timestamps_relevantes: lista de {{timestamp_inicio, timestamp_fim, descricao}}\n\n"
    "Instruções:\n"
    "- Identifique claramente sinais de negócio (ex: 'me manda a proposta', 'vamos marcar').\n"
    "- NUNCA prometa rentabilidade.\n"
    "- Timestamps no formato MM:SS ou HH:MM:SS."
)

PROMPT_MARKETING = (
    "Você está analisando um VÍDEO DE MARKETING/CONTEÚDO para prospectação.\n\n"
    "Base:\n"
    "- Transcrição:\n{transcript}\n\n"
    "- Texto extraído de slides/tela (se houver):\n{slides_text}\n\n"
    "Gere um briefing estruturado em JSON com:\n"
    "tipo: 'marketing'\n"
    "titulo: sugestão de título\n"
    "tema_central: o que o vídeo aborda\n"
    "gancho_principal: o melhor gancho para capturar atenção\n"
    "ganhos_secundarios: 2-3 outros ganchos\n"
    "cta_natural: call-to-action natural para assessoria\n"
    "tom_sugerido: tom de voz/escrita\n"
    "publico_alvo: quem se beneficia\n"
    "duracao_sugerida: duração ideal se for recortar\n"
    "cortes_ideais: lista de {{inicio, fim, hook, cta}}\n"
    "observacoes_regulatórias: cuidados com promessas de rentabilidade"
)

PROMPT_GENERIC = (
    "Você está resumindo um vídeo genérico.\n\n"
    "Base:\n"
    "- Transcrição:\n{transcript}\n\n"
    "- Texto extraído de slides/tela (se houver):\n{slides_text}\n\n"
    "Gere um resumo estruturado em JSON com:\n"
    "tipo: 'outro'\n"
    "titulo: título sugerido\n"
    "resumo_executivo: resumo em 2-4 frases\n"
    "temas_principais: lista de tópicos\n"
    "informacoes_relevantes: detalhes importantes\n"
    "proximos_passos: o que fazer com essa informação\n"
    "duvidas_pendentes: perguntas que ficaram"
)

MINUTE_SCHEMA_HINT = {
    "tipo": "string",
    "titulo": "string",
    "participantes": ["string"],
    "resumo_executivo": "string",
    "temas_discutidos": ["string"],
    "decisoes": ["string"],
    "action_items": [{"quem": "string", "o_que": "string", "ate_quando": "string"}],
    "proximos_passos": ["string"],
    "riscos_regulatórios": ["string"],
    "oportunidades_negocio": ["string"],
    "duvidas_pendentes": ["string"],
    "timestamps_relevantes": [{"timestamp_inicio": "string", "timestamp_fim": "string", "descricao": "string"}],
}

CLASSIFY_SCHEMA_HINT = {
    "context_type": "lead|escritorio|marketing|outro",
    "confidence": 0.0,
    "motivo": "string",
}

# ── FFmpeg helpers ─────────────────────────────────────────────────────


def _run_ffmpeg(args: list[str], timeout: int = 300) -> tuple[bool, str]:
    """Executa comando ffmpeg e retorna (sucesso, stderr)."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode == 0, proc.stderr
    except subprocess.TimeoutExpired:
        return False, "FFmpeg timeout"
    except Exception as exc:
        return False, str(exc)


def _video_duration_seconds(video_path: str) -> float:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return float(proc.stdout.strip())
    except Exception:
        return 0.0


def _format_duration(seconds: float) -> str:
    if not seconds or seconds < 0:
        return ""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs:
        return f"{hrs}h{mins:02d}m{secs:02d}s"
    return f"{mins}m{secs:02d}s"


def _parse_timestamp_to_seconds(ts: str) -> float:
    """Converte HH:MM:SS, MM:SS ou SS em segundos (float)."""
    if not ts:
        return 0.0
    ts = str(ts).strip().replace(",", ".")
    # Aceita 00:01:23.450
    parts = ts.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return 0.0


def _seconds_to_timestamp(seconds: float) -> str:
    """Converte segundos em HH:MM:SS ou MM:SS."""
    if not seconds or seconds < 0:
        return "00:00"
    secs = int(seconds)
    ms = int((seconds - secs) * 100)
    mins = (secs // 60) % 60
    hrs = secs // 3600
    s = secs % 60
    if hrs:
        return f"{hrs:02d}:{mins:02d}:{s:02d}"
    return f"{mins:02d}:{s:02d}"


def _build_video_filter(aspect: str) -> str | None:
    """Retorna filtro de vídeo para corte no aspecto desejado."""
    aspect = (aspect or "original").lower()
    if aspect == "vertical":
        # 9:16 (1080x1920), mantém vídeo centralizado com pad
        return (
            "crop=ih*9/16:ih,scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
        )
    if aspect == "square":
        return (
            "crop=ih:ih,scale=1080:1080:force_original_aspect_ratio=decrease,"
            "pad=1080:1080:(ow-iw)/2:(oh-ih)/2:black"
        )
    return None


def _render_cut(
    video_path: str,
    start: float,
    end: float,
    output_path: str,
    aspect: str = "original",
) -> tuple[bool, str]:
    """Renderiza um corte do vídeo original entre start e end (segundos)."""
    if start < 0:
        start = 0.0
    if end <= start:
        return False, "Timestamp final deve ser maior que o inicial."

    vf = _build_video_filter(aspect)
    args = [
        "-ss",
        str(start),
        "-to",
        str(end),
        "-i",
        video_path,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
    ]
    if vf:
        args += ["-vf", vf]
    args.append(output_path)

    return _run_ffmpeg(args, timeout=300)


def _extract_audio(video_path: str, audio_path: str) -> tuple[bool, str]:
    return _run_ffmpeg(
        [
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-b:a",
            "32k",
            audio_path,
        ]
    )


def _extract_frames(video_path: str, output_dir: Path, interval_seconds: int = 8) -> tuple[bool, list[str], str]:
    """Extrai frames a cada N segundos."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "frame_%04d.jpg")
    success, err = _run_ffmpeg(
        [
            "-i",
            video_path,
            "-vf",
            f"fps=1/{interval_seconds}",
            "-q:v",
            "2",
            pattern,
        ]
    )
    if not success:
        return False, [], err
    frames = sorted(str(p) for p in output_dir.glob("frame_*.jpg"))
    return True, frames, ""


def _select_key_frames(video_path: str, output_dir: Path, max_frames: int = 8) -> tuple[bool, list[str], str]:
    """Seleciona frames-chave por mudança de cena (threshold baixo para slides)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    scene_path = output_dir / "scene_%03d.jpg"
    success, err = _run_ffmpeg(
        [
            "-i",
            video_path,
            "-vf",
            "select='gt(scene,0.15)',scale=1280:-1",
            "-vsync",
            "vfr",
            "-q:v",
            "2",
            str(scene_path),
        ],
        timeout=300,
    )
    if not success:
        # fallback para intervalos
        return _extract_frames(video_path, output_dir, interval_seconds=10)
    frames = sorted(str(p) for p in output_dir.glob("scene_*.jpg"))
    if not frames:
        return _extract_frames(video_path, output_dir, interval_seconds=10)
    # Limita frames para não sobrecarregar OCR
    if len(frames) > max_frames:
        step = len(frames) // max_frames
        frames = frames[::step][:max_frames]
    return True, frames, ""


# ── Tesseract / OCR ────────────────────────────────────────────────────


def _configure_tesseract_env() -> None:
    tesseract_cmd = os.getenv(
        "TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    )
    if not Path(tesseract_cmd).exists():
        for candidate in [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Users\Juan\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
        ]:
            if Path(candidate).exists():
                tesseract_cmd = candidate
                break
    tessdata_prefix = os.getenv("TESSDATA_PREFIX")
    if not tessdata_prefix:
        default_tessdata = Path(tesseract_cmd).parent / "tessdata"
        user_tessdata = Path.home() / "AppData" / "Local" / "Tesseract-OCR" / "tessdata"
        if (user_tessdata / "por.traineddata").exists():
            tessdata_prefix = str(user_tessdata.resolve())
        elif (default_tessdata / "por.traineddata").exists():
            tessdata_prefix = str(default_tessdata.resolve())
    if tesseract_cmd:
        os.environ["TESSERACT_CMD"] = tesseract_cmd
    if tessdata_prefix:
        os.environ["TESSDATA_PREFIX"] = tessdata_prefix


def _ocr_image(image_path: str) -> str:
    try:
        from PIL import Image
        import pytesseract

        _configure_tesseract_env()
        tesseract_cmd = os.getenv("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if Path(tesseract_cmd).exists():
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        image = Image.open(image_path)
        return pytesseract.image_to_string(image, lang="por").strip()
    except Exception as exc:
        return f""


def _ocr_frames(frame_paths: list[str]) -> str:
    if not frame_paths:
        return ""
    chunks: list[str] = []
    for path in frame_paths:
        text = _ocr_image(path)
        if text and len(text) > 20:
            chunks.append(f"[FRAME {Path(path).name}]\n{text}")
    return "\n\n".join(chunks)


# ── Gladia transcription ───────────────────────────────────────────────


def _transcribe_with_gladia(audio_path: str) -> dict[str, Any]:
    """Transcreve áudio MP3 usando Gladia API v2 com diarização e timestamps."""
    api_key = os.getenv("GLADIA_API_KEY")
    if not api_key:
        return {"error": "GLADIA_API_KEY não configurada", "full_text": "", "utterances": []}

    headers = {"x-gladia-key": api_key}
    boundary = "----VideoSkillBoundary"

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="audio.mp3"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n"
    ).encode("utf-8") + audio_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    upload_headers = {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"}

    try:
        req = urllib.request.Request(
            "https://api.gladia.io/v2/upload/",
            data=body,
            headers=upload_headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            upload_data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": f"Erro upload Gladia: {exc}", "full_text": "", "utterances": []}

    audio_url = upload_data.get("audio_url")
    if not audio_url:
        return {"error": "Gladia não retornou audio_url", "full_text": "", "utterances": []}

    payload = json.dumps(
        {
            "audio_url": audio_url,
            "diarization": True,
        }
    ).encode("utf-8")

    try:
        req2 = urllib.request.Request(
            "https://api.gladia.io/v2/pre-recorded/",
            data=payload,
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req2, timeout=120) as resp:
            pre = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": f"Erro iniciar transcrição Gladia: {exc}", "full_text": "", "utterances": []}

    result_url = pre.get("result_url")
    if not result_url:
        return {"error": "Gladia não retornou result_url", "full_text": "", "utterances": []}

    max_polls = 90  # ~3 minutos
    for attempt in range(max_polls):
        try:
            req3 = urllib.request.Request(result_url, headers=headers, method="GET")
            with urllib.request.urlopen(req3, timeout=60) as resp:
                poll = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return {"error": f"Erro polling Gladia: {exc}", "full_text": "", "utterances": []}

        status = poll.get("status", "")
        if status in ("done", "completed"):
            transcription = poll.get("result", {}).get("transcription", {})
            utterances_raw = transcription.get("utterances", [])
            utterances = []
            for u in utterances_raw:
                text = (u.get("text") or "").strip()
                if not text:
                    continue
                utterances.append(
                    {
                        "speaker": u.get("speaker", "?"),
                        "start": u.get("start", 0.0),
                        "end": u.get("end", 0.0),
                        "text": text,
                    }
                )
            full_text = transcription.get("full_transcript", "")
            if not full_text and utterances:
                full_text = " ".join(u["text"] for u in utterances)
            return {
                "full_text": full_text,
                "utterances": utterances,
                "error": None,
                "raw_response": poll,
            }

        if status in ("failed", "error"):
            error_code = poll.get("error_code") or "desconhecido"
            return {"error": f"Transcrição falhou: {error_code}", "full_text": "", "utterances": []}

        time.sleep(2)

    return {"error": "Timeout aguardando transcrição", "full_text": "", "utterances": []}


def _format_transcript_for_llm(transcript_data: dict[str, Any]) -> str:
    utterances = transcript_data.get("utterances", [])
    if not utterances:
        return transcript_data.get("full_text", "")
    lines = []
    for u in utterances:
        start = u.get("start", 0.0)
        mins = int(start // 60)
        secs = int(start % 60)
        speaker = u.get("speaker", "?")
        text = u.get("text", "")
        lines.append(f"[{mins:02d}:{secs:02d}] {speaker}: {text}")
    return "\n".join(lines)


# ── LLM helpers ────────────────────────────────────────────────────────


def _llm_classify(transcript_data: dict[str, Any], caption: str = "") -> dict[str, Any]:
    llm = UnifiedLLMClient(timeout=180)
    transcript_text = _format_transcript_for_llm(transcript_data)
    prompt = (
        PROMPT_CLASSIFY
        .replace("{transcript}", transcript_text[:4000])
        .replace("{caption}", caption or "Nenhuma")
    )
    result = llm.extract_json(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=400,
        schema_hint=CLASSIFY_SCHEMA_HINT,
    )
    parsed = result.get("parsed") or {}
    if not parsed or parsed.get("context_type") not in ("lead", "escritorio", "marketing", "outro"):
        return {"context_type": "outro", "confidence": 0.0, "motivo": "Não foi possível classificar"}
    return {
        "context_type": parsed.get("context_type"),
        "confidence": float(parsed.get("confidence", 0)),
        "motivo": parsed.get("motivo", ""),
    }


def _render_template(template: str, transcript: str, slides_text: str) -> str:
    """Substitui placeholders sem usar .format(), evitando conflitos com JSON."""
    return template.replace("{transcript}", transcript).replace("{slides_text}", slides_text)


def _llm_minute(transcript_data: dict[str, Any], slides_text: str, context_type: str) -> dict[str, Any]:
    llm = UnifiedLLMClient(timeout=240)
    transcript_text = _format_transcript_for_llm(transcript_data)
    slides_text = (slides_text or "")[:2500]

    if context_type == "lead":
        prompt = _render_template(PROMPT_LEAD_MEETING, transcript_text[:5000], slides_text)
        schema = {**MINUTE_SCHEMA_HINT, "objecoes": ["string"], "sinais_de_interesse": ["string"]}
    elif context_type == "escritorio":
        prompt = _render_template(PROMPT_OFFICE_MEETING, transcript_text[:5000], slides_text)
        schema = MINUTE_SCHEMA_HINT
    elif context_type == "marketing":
        prompt = _render_template(PROMPT_MARKETING, transcript_text[:5000], slides_text)
        schema = {
            "tipo": "string",
            "titulo": "string",
            "tema_central": "string",
            "gancho_principal": "string",
            "ganhos_secundarios": ["string"],
            "cta_natural": "string",
            "tom_sugerido": "string",
            "publico_alvo": "string",
            "duracao_sugerida": "string",
            "cortes_ideais": [{"inicio": "string", "fim": "string", "hook": "string", "cta": "string"}],
            "observacoes_regulatórias": "string",
        }
    else:
        prompt = _render_template(PROMPT_GENERIC, transcript_text[:5000], slides_text)
        schema = {
            "tipo": "string",
            "titulo": "string",
            "resumo_executivo": "string",
            "temas_principais": ["string"],
            "informacoes_relevantes": ["string"],
            "proximos_passos": ["string"],
            "duvidas_pendentes": ["string"],
        }

    messages = [
        {"role": "system", "content": SYSTEM_BASE},
        {"role": "user", "content": prompt},
    ]
    result = llm.extract_json(
        messages=messages,
        temperature=0.4,
        max_tokens=2500,
        schema_hint=schema,
    )
    parsed = result.get("parsed") or {}
    parsed["tipo"] = context_type
    return parsed


def _llm_suggest_cuts(transcript_text: str, duration_hint: str = "") -> str:
    prompt = (
        "Você é editor de vídeos curtos para prospectação de assessoria de investimentos.\n"
        "Receba a transcrição de um vídeo longo e sugira 3 a 5 cortes para Reels/TikTok/Shorts.\n"
        "Para cada corte, informe: início, fim, hook sugerido, CTA e tomada de decisão.\n\n"
        f"Transcrição:\n{transcript_text}\n"
    )
    if duration_hint:
        prompt += f"\nDuração aproximada: {duration_hint}\n"
    llm = UnifiedLLMClient(timeout=180)
    resp = llm.complete(prompt, temperature=0.7, max_tokens=1500)
    return resp.content if resp and not resp.error else f"[Erro LLM: {resp.error if resp else 'null'}]"


CUTS_SCHEMA_HINT = {
    "cortes": [
        {
            "inicio": "string (MM:SS ou HH:MM:SS)",
            "fim": "string (MM:SS ou HH:MM:SS)",
            "titulo": "string (máx 60 chars)",
            "hook": "string (primeiras palavras/fala do corte)",
            "cta": "string (call-to-action natural)",
        }
    ]
}


def _llm_generate_cuts(
    transcript_text: str,
    duration_seconds: float,
    n_cuts: int = 3,
    target_duration: str = "30-60s",
) -> list[dict[str, Any]]:
    """Gera cortes estruturados com timestamps para renderização via LLM."""
    duration_str = _format_duration(duration_seconds)
    prompt = (
        "Você é editor de vídeos curtos para prospectação de assessoria de investimentos.\n"
        "Analise a transcrição abaixo e gere EXATAMENTE {n_cuts} cortes para vídeos curtos "
        "(Reels/TikTok/Shorts) com duração de {target_duration} cada.\n\n"
        "Regras:\n"
        "- Cada corte deve ser um trecho CONTÍNUO do vídeo original.\n"
        "- Início e fim devem estar dentro da duração total do vídeo.\n"
        "- Formato de tempo: MM:SS ou HH:MM:SS.\n"
        "- Escolha trechos com gancho forte no início e CTA claro no final.\n"
        "- Respeite o enquadramento regulatório: NUNCA prometa rentabilidade.\n\n"
        "Responda APENAS com um JSON no formato:\n"
        '{"cortes": [{"inicio": "0:12", "fim": "0:52", "titulo": "...", "hook": "...", "cta": "..."}]}\n\n'
        f"Duração total do vídeo: {duration_str}\n"
        f"Transcrição:\n{transcript_text[:6000]}\n"
    ).replace("{n_cuts}", str(n_cuts)).replace("{target_duration}", target_duration)

    llm = UnifiedLLMClient(timeout=180)
    result = llm.extract_json(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=1800,
        schema_hint=CUTS_SCHEMA_HINT,
    )
    parsed = result.get("parsed") or {}
    cortes = parsed.get("cortes") or []

    # Normaliza e valida cada corte
    valid_cuts: list[dict[str, Any]] = []
    for c in cortes:
        start = _parse_timestamp_to_seconds(c.get("inicio", "0"))
        end = _parse_timestamp_to_seconds(c.get("fim", "0"))
        if end <= start or end > duration_seconds + 1:
            continue
        valid_cuts.append({
            "inicio": _seconds_to_timestamp(start),
            "fim": _seconds_to_timestamp(end),
            "inicio_seconds": start,
            "fim_seconds": end,
            "titulo": str(c.get("titulo", "Corte")).strip() or "Corte",
            "hook": str(c.get("hook", "")).strip(),
            "cta": str(c.get("cta", "")).strip(),
        })
    return valid_cuts[:5]


def _normalize_cuts(cuts: list[dict[str, Any]], duration_seconds: float) -> list[dict[str, Any]]:
    """Garante que cortes tenham inicio_seconds/fim_seconds normalizados."""
    normalized: list[dict[str, Any]] = []
    for c in cuts:
        start = float(c.get("inicio_seconds") or _parse_timestamp_to_seconds(c.get("inicio", "0")))
        end = float(c.get("fim_seconds") or _parse_timestamp_to_seconds(c.get("fim", "0")))
        if end <= start:
            continue
        if end > duration_seconds + 1:
            end = duration_seconds
        normalized.append({
            "inicio": _seconds_to_timestamp(start),
            "fim": _seconds_to_timestamp(end),
            "inicio_seconds": start,
            "fim_seconds": end,
            "titulo": str(c.get("titulo", "Corte")).strip() or "Corte",
            "hook": str(c.get("hook", "")).strip(),
            "cta": str(c.get("cta", "")).strip(),
        })
    return normalized


def _apply_cuts(
    video_path: str,
    cuts: list[dict[str, Any]],
    output_dir: Path,
    aspect: str = "original",
) -> list[dict[str, Any]]:
    """Renderiza cada corte em MP4 e retorna metadados incluindo caminhos."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []
    for idx, cut in enumerate(cuts, start=1):
        start = float(cut.get("inicio_seconds", 0))
        end = float(cut.get("fim_seconds", 0))
        output_path = output_dir / f"cut_{idx:02d}_{start:.0f}s_{end:.0f}s.mp4"
        success, err = _render_cut(video_path, start, end, str(output_path), aspect=aspect)
        rendered.append({
            "index": idx,
            "cut": cut,
            "output_path": str(output_path) if success else None,
            "success": success,
            "error": err if not success else None,
        })
    return rendered


# ── Main processing ────────────────────────────────────────────────────


def _detect_context_hint_from_caption(caption: str) -> str | None:
    if not caption:
        return None
    c = caption.lower()
    if any(w in c for w in ["lead", "cliente", "prospect", "reunião com", "reuniao com", "ligação com", "ligacao com"]):
        return "lead"
    if any(w in c for w in ["escritorio", "escritório", "interna", "consorcio", "consórcio", "produto", "processo", "1a", "xp"]):
        return "escritorio"
    if any(w in c for w in ["marketing", "reels", "tiktok", "shorts", "conteudo", "conteúdo", "hook", "cta"]):
        return "marketing"
    return None


def process_video(
    video_path: str,
    action: str = "summarize",
    caption: str = "",
    context_hint: str = "auto",
    interval_seconds: int = 8,
    extract_slides: bool = True,
    max_slide_frames: int = 8,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Processa vídeo e retorna minuta/transcrição/frames/cortes.

    context_hint: auto | lead | escritorio | marketing | outro
    """
    if not os.path.exists(video_path):
        return {"success": False, "error": f"Arquivo não encontrado: {video_path}", "action": action}

    duration_seconds = _video_duration_seconds(video_path)
    duration = _format_duration(duration_seconds)

    payload: dict[str, Any] = kwargs
    result: dict[str, Any] = {
        "success": True,
        "action": action,
        "video_path": video_path,
        "duration": duration,
        "duration_seconds": duration_seconds,
    }

    # ── extract_frames ──
    if action == "extract_frames":
        output_dir = Path.home() / "AppData" / "Local" / "lead_prospecting_engine" / "video_frames"
        success, frames, err = _extract_frames(video_path, output_dir, interval_seconds)
        if not success:
            return {"success": False, "error": f"Erro ao extrair frames: {err}", "action": action}
        result["frames_dir"] = str(output_dir)
        result["frames"] = frames
        result["message"] = f"🎞️ {len(frames)} frames extraídos em: {output_dir}"
        return result

    # ── suggest_cuts ──
    if action == "suggest_cuts":
        transcribe_result = process_video(video_path, action="transcribe")
        if not transcribe_result.get("success"):
            return transcribe_result
        transcript_text = transcribe_result.get("transcript", {}).get("full_text", "")
        cuts = _llm_suggest_cuts(transcript_text, duration)
        result["transcript"] = transcribe_result.get("transcript")
        result["cuts"] = cuts
        result["message"] = "✂️ Cortes sugeridos para Shorts/Reels."
        return result

    # ── render_cuts ──
    if action == "render_cuts":
        cuts = payload.get("cuts", [])
        if not cuts:
            return {"success": False, "error": "Nenhum corte informado para renderização.", "action": action}
        # Normaliza timestamps vindos do payload
        cuts = _normalize_cuts(cuts, duration_seconds)
        output_dir = Path(payload.get("output_dir") or (Path.home() / "AppData" / "Local" / "lead_prospecting_engine" / "video_cuts"))
        aspect = payload.get("aspect", "original")
        rendered = _apply_cuts(video_path, cuts, output_dir, aspect=aspect)
        success_count = sum(1 for r in rendered if r["success"])
        result["cuts"] = rendered
        result["output_dir"] = str(output_dir)
        result["aspect"] = aspect
        result["message"] = f"✂️ {success_count}/{len(rendered)} cortes renderizados em {output_dir}."
        result["success"] = success_count > 0
        if not result["success"]:
            result["error"] = "Nenhum corte foi renderizado."
        return result

    # ── generate_and_render_cuts ──
    if action == "generate_and_render_cuts":
        transcribe_result = process_video(video_path, action="transcribe")
        if not transcribe_result.get("success"):
            return transcribe_result
        transcript_text = transcribe_result.get("transcript", {}).get("full_text", "")
        n_cuts = int(payload.get("n_cuts", 3))
        target_duration = payload.get("target_duration", "30-60s")
        aspect = payload.get("aspect", "original")
        output_dir = Path(payload.get("output_dir") or (Path.home() / "AppData" / "Local" / "lead_prospecting_engine" / "video_cuts"))

        cuts = _llm_generate_cuts(
            transcript_text,
            duration_seconds,
            n_cuts=n_cuts,
            target_duration=target_duration,
        )
        if not cuts:
            return {
                "success": False,
                "error": "Não foi possível gerar cortes a partir da transcrição.",
                "action": action,
                "transcript": transcribe_result.get("transcript"),
            }
        rendered = _apply_cuts(video_path, cuts, output_dir, aspect=aspect)
        success_count = sum(1 for r in rendered if r["success"])
        result["transcript"] = transcribe_result.get("transcript")
        result["generated_cuts"] = cuts
        result["rendered_cuts"] = rendered
        result["output_dir"] = str(output_dir)
        result["aspect"] = aspect
        result["message"] = f"✂️ {success_count}/{len(rendered)} cortes gerados e renderizados em {output_dir}."
        result["success"] = success_count > 0
        if not result["success"]:
            result["error"] = "Nenhum corte foi renderizado."
        return result

    # ── transcribe e summarize compartilham pipeline de áudio ──
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.mp3")
        success, err = _extract_audio(video_path, audio_path)
        if not success:
            return {"success": False, "error": f"Erro ao extrair áudio: {err}", "action": action}

        transcript_data = _transcribe_with_gladia(audio_path)
        if transcript_data.get("error"):
            return {
                "success": False,
                "error": transcript_data["error"],
                "action": action,
            }

        result["transcript"] = transcript_data

        if action == "transcribe":
            result["message"] = "🎬 Vídeo transcrito com sucesso."
            return result

        # ── summarize: extração de slides + minuta ──
        slides_text = ""
        if extract_slides:
            try:
                frames_dir = Path(tmpdir) / "frames"
                success, frames, _ = _select_key_frames(video_path, frames_dir, max_frames=max_slide_frames)
                if success and frames:
                    slides_text = _ocr_frames(frames)
                    result["slides_frames"] = frames
            except Exception:
                slides_text = ""
        result["slides_text"] = slides_text

        # Resolve context_hint
        auto_classification = None
        effective_context = context_hint
        if effective_context == "auto":
            detected = _detect_context_hint_from_caption(caption)
            if detected:
                effective_context = detected
            else:
                auto_classification = _llm_classify(transcript_data, caption)
                if auto_classification.get("confidence", 0) >= 0.75:
                    effective_context = auto_classification["context_type"]
                else:
                    result["ask_classification"] = True
                    result["suggested_context"] = auto_classification
                    result["message"] = "🎬 Vídeo processado. Preciso que você confirme o tipo para gerar a minuta."
                    result["options"] = ["lead", "escritorio", "marketing", "outro"]
                    return result

        minute = _llm_minute(transcript_data, slides_text, effective_context)
        result["context_type"] = effective_context
        result["minute"] = minute
        result["ask_classification"] = False
        result["message"] = f"🎬 Minuta de {effective_context} gerada com sucesso."

        # Prepara candidatos de salvamento
        result["save_candidates"] = _build_save_candidates(effective_context, minute, transcript_data)

        # Flag padronizada de aprovação + drafts explícitos para o cockpit
        result["requires_approval"] = True
        result["draft"] = _build_drafts(effective_context, minute, transcript_data)

    return result


def _build_save_candidates(context_type: str, minute: dict[str, Any], transcript_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Sugere o que pode ser salvo no CRM a partir da minuta."""
    candidates = []
    if context_type == "lead":
        candidates.append({
            "target": "interaction",
            "description": "Registrar resumo da reunião no histórico do lead",
        })
        oportunidades = minute.get("oportunidades_negocio", [])
        sinais = minute.get("sinais_de_interesse", [])
        if oportunidades or sinais:
            candidates.append({
                "target": "opportunity",
                "description": "Criar/atualizar oportunidade comercial",
            })
            candidates.append({
                "target": "alert",
                "description": "Criar alerta de sinal de negócio no painel",
            })
        if minute.get("action_items") or minute.get("proximos_passos"):
            candidates.append({
                "target": "meeting",
                "description": "Atualizar meeting com outcome e next steps",
            })
    elif context_type == "escritorio":
        candidates.append({
            "target": "knowledge",
            "description": "Salvar como nota interna/conhecimento do escritório",
        })
        if minute.get("oportunidades_negocio"):
            candidates.append({
                "target": "alert",
                "description": "Alerta: oportunidade de negócio detectada na reunião interna",
            })
    elif context_type == "marketing":
        candidates.append({
            "target": "marketing_brief",
            "description": "Salvar briefing de conteúdo para reuso",
        })
    return candidates


def _build_drafts(context_type: str, minute: dict[str, Any], transcript_data: dict[str, Any]) -> dict[str, Any]:
    """Prepara drafts prontos para persistência após aprovação."""
    drafts: dict[str, Any] = {}
    if context_type == "lead":
        drafts["interaction"] = {
            "entity_type": "interaction",
            "summary": minute.get("resumo_executivo"),
            "next_steps": minute.get("proximos_passos"),
            "action_items": minute.get("action_items"),
        }
        if minute.get("oportunidades_negocio") or minute.get("sinais_de_interesse"):
            drafts["opportunity"] = {
                "entity_type": "opportunity",
                "title": minute.get("titulo") or "Oportunidade de reunião",
                "stage": "prospect",
                "description": "\n".join(minute.get("oportunidades_negocio", []))[:500],
            }
            drafts["alert"] = {
                "entity_type": "alert",
                "title": f"Sinal de negócio: {minute.get('titulo', 'Reunião')}",
                "description": "Sinais de interesse: " + "; ".join(minute.get("sinais_de_interesse", []))[:300],
                "priority": "high",
            }
    elif context_type == "escritorio":
        drafts["knowledge"] = {
            "entity_type": "knowledge",
            "title": minute.get("titulo") or "Nota interna",
            "content": minute.get("resumo_executivo"),
            "tags": ["reunião interna", "escritório"],
        }
    elif context_type == "marketing":
        drafts["marketing_brief"] = {
            "entity_type": "marketing_brief",
            "title": minute.get("titulo") or "Brief de conteúdo",
            "gancho_principal": minute.get("gancho_principal"),
            "cta_natural": minute.get("cta_natural"),
            "cortes_ideais": minute.get("cortes_ideais", []),
        }
    return drafts


# Alias para o dispatcher padrão do Hermes
run = process_video


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

    video_path = payload.get("video_path", "")
    if not video_path:
        print(json.dumps({"success": False, "error": "video_path é obrigatório"}))
        sys.exit(1)

    result = process_video(
        video_path=video_path,
        action=payload.get("action", "summarize"),
        caption=payload.get("caption", ""),
        context_hint=payload.get("context_hint", "auto"),
        interval_seconds=payload.get("interval_seconds", 8),
        extract_slides=payload.get("extract_slides", True),
        max_slide_frames=payload.get("max_slide_frames", 8),
        # render / generate_and_render
        cuts=payload.get("cuts"),
        n_cuts=payload.get("n_cuts", 3),
        target_duration=payload.get("target_duration", "30-60s"),
        aspect=payload.get("aspect", "original"),
        output_dir=payload.get("output_dir"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
