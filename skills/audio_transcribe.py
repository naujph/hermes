#!/usr/bin/env python3
"""Skill: audio_transcribe

Transcreve áudio de forma inteligente e rápida:
- Áudios curtos (<60s): faster-whisper tiny local (1-3s em CPU)
- Áudios longos ou falha local: Gladia cloud
- Fallback final: faster-whisper base local

Recebe JSON via stdin:
    {"audio_path": "/caminho/audio.webm"}

Retorna JSON via stdout:
    {
        "success": true,
        "transcript": "texto transcrito",
        "method": "whisper_tiny|gladia|whisper_base",
        "duration_seconds": 12.5,
        "logs": [...]
    }
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

GLADIA_API_KEY = os.getenv("GLADIA_API_KEY", "")
GLADIA_ENDPOINT = "https://api.gladia.io/v2/transcription"
GLADIA_POLL_INTERVAL = 2
GLADIA_MAX_POLL = 30

SHORT_AUDIO_THRESHOLD_SECONDS = 60

# Cache de modelos Whisper para evitar recarregar a cada transcrição.
_WHISPER_MODELS: dict[str, Any] = {}


def is_backend_available(backend: str) -> bool:
    """Verifica se um backend de transcrição está disponível."""
    backend = backend.lower()
    if backend in ("gladia",):
        return bool(GLADIA_API_KEY)
    if backend in ("whisper", "whisper_tiny", "whisper_base", "local"):
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception:
            return False
    return False


def _get_audio_duration(audio_path: str) -> float:
    """Tenta obter duração do áudio com ffprobe/ffmpeg, senão estima."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass

    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(audio_path)
        return len(audio) / 1000.0
    except Exception:
        return 0.0


def _convert_to_wav(input_path: str, output_path: str | None = None) -> str:
    """Converte para WAV mono 16kHz."""
    from pydub import AudioSegment
    audio = AudioSegment.from_file(input_path)
    audio = audio.set_channels(1).set_frame_rate(16000)
    if output_path is None:
        output_path = tempfile.mktemp(suffix=".wav")
    audio.export(output_path, format="wav")
    return output_path


def _transcribe_with_whisper(audio_path: str, model_size: str = "tiny") -> tuple[str | None, list[str]]:
    """Transcreve com faster-whisper local (modelo cacheado em memória)."""
    logs = [f"[STT] Tentando Whisper local ({model_size})..."]
    try:
        from faster_whisper import WhisperModel
        if model_size not in _WHISPER_MODELS:
            logs.append(f"[STT] Carregando modelo {model_size}...")
            _WHISPER_MODELS[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
        model = _WHISPER_MODELS[model_size]
        segments, info = model.transcribe(audio_path, language="pt", beam_size=5, vad_filter=True)
        transcript = " ".join([seg.text for seg in segments]).strip()
        logs.append(f"[STT] Whisper {model_size} OK ({info.language}, {info.language_probability:.2f})")
        return transcript, logs
    except Exception as exc:
        logs.append(f"[STT] Whisper {model_size} falhou: {exc}")
        # Remove do cache para permitir retry limpo na próxima vez
        _WHISPER_MODELS.pop(model_size, None)
        return None, logs


def _transcribe_with_gladia(audio_path: str) -> tuple[str | None, list[str]]:
    """Transcreve com Gladia API v2 (async)."""
    logs = ["[STT] Tentando Gladia cloud..."]
    if not GLADIA_API_KEY:
        logs.append("[STT] Gladia API key não configurada.")
        return None, logs

    ext = Path(audio_path).suffix.lower()
    wav_path = audio_path
    if ext != ".wav":
        try:
            wav_path = _convert_to_wav(audio_path)
            logs.append("[STT] Convertido para WAV para Gladia.")
        except Exception as exc:
            logs.append(f"[STT] Falha ao converter para WAV: {exc}")
            return None, logs

    try:
        boundary = "----GladiaBoundary"
        file_bytes = Path(wav_path).read_bytes()
        filename = Path(wav_path).name

        body_lines = [
            f"--{boundary}".encode(),
            f'Content-Disposition: form-data; name="audio"; filename="{filename}"'.encode(),
            b"Content-Type: audio/wav",
            b"",
            file_bytes,
            f"--{boundary}--".encode(),
        ]
        body = b"\r\n".join(body_lines)

        req = urllib.request.Request(
            GLADIA_ENDPOINT,
            data=body,
            headers={
                "x-gladia-key": GLADIA_API_KEY,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        result_url = data.get("result_url")
        if not result_url:
            logs.append(f"[STT] Gladia sem result_url: {data}")
            return None, logs

        for attempt in range(GLADIA_MAX_POLL):
            time.sleep(GLADIA_POLL_INTERVAL)
            poll_req = urllib.request.Request(
                result_url,
                headers={"x-gladia-key": GLADIA_API_KEY},
                method="GET",
            )
            with urllib.request.urlopen(poll_req, timeout=30) as poll_resp:
                poll_data = json.loads(poll_resp.read().decode("utf-8"))

            status = poll_data.get("status")
            if status == "done":
                transcript = poll_data.get("result", {}).get("transcription", {}).get("full_transcript", "")
                logs.append("[STT] Gladia OK.")
                return transcript.strip(), logs
            elif status == "error":
                logs.append(f"[STT] Gladia erro: {poll_data}")
                return None, logs
            logs.append(f"[STT] Gladia status: {status} ({attempt + 1}/{GLADIA_MAX_POLL})")

        logs.append("[STT] Gladia timeout.")
        return None, logs
    except Exception as exc:
        logs.append(f"[STT] Gladia falhou: {exc}")
        return None, logs
    finally:
        if ext != ".wav" and wav_path != audio_path:
            try:
                os.remove(wav_path)
            except Exception:
                pass


def transcribe_audio(audio_path: str) -> dict[str, Any]:
    """Orquestra a transcrição com a melhor estratégia."""
    logs = []
    start = time.time()
    audio_path = str(Path(audio_path).resolve())

    if not Path(audio_path).exists():
        return {"success": False, "transcript": "", "method": "none", "duration_seconds": 0, "logs": ["Arquivo não encontrado."]}

    duration = _get_audio_duration(audio_path)
    logs.append(f"[STT] Duração estimada: {duration:.1f}s")

    # Estratégia: áudio curto -> whisper tiny local (mais rápido)
    method = "none"
    transcript = None

    if duration < SHORT_AUDIO_THRESHOLD_SECONDS:
        transcript, wlogs = _transcribe_with_whisper(audio_path, model_size="tiny")
        logs.extend(wlogs)
        if transcript is not None:
            method = "whisper_tiny"
            # Áudios curtos que rodaram localmente e retornaram vazio são tratados como
            # silêncio/inaudível: não vale a pena esperar fallback cloud/lento.
            if not transcript.strip():
                logs.append("[STT] Nenhuma fala detectada no áudio curto.")
                elapsed = time.time() - start
                logs.append(f"[STT] Tempo total: {elapsed:.1f}s | método: {method}")
                return {
                    "success": True,
                    "transcript": "",
                    "method": method,
                    "duration_seconds": duration,
                    "elapsed_seconds": round(elapsed, 2),
                    "logs": logs,
                }

    # Se não conseguiu local ou áudio é longo, tenta Gladia
    if not transcript:
        transcript, glogs = _transcribe_with_gladia(audio_path)
        logs.extend(glogs)
        if transcript:
            method = "gladia"

    # Fallback final: whisper base
    if not transcript:
        transcript, wlogs = _transcribe_with_whisper(audio_path, model_size="base")
        logs.extend(wlogs)
        if transcript:
            method = "whisper_base"

    elapsed = time.time() - start
    logs.append(f"[STT] Tempo total: {elapsed:.1f}s | método: {method}")

    if not transcript:
        return {"success": False, "transcript": "", "method": method, "duration_seconds": duration, "elapsed_seconds": round(elapsed, 2), "logs": logs}

    return {
        "success": True,
        "transcript": transcript,
        "method": method,
        "duration_seconds": duration,
        "elapsed_seconds": round(elapsed, 2),
        "logs": logs,
    }


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "transcript": "", "method": "none", "duration_seconds": 0, "logs": ["JSON inválido"]}, ensure_ascii=False))
        sys.exit(1)

    audio_path = payload.get("audio_path")
    if not audio_path:
        print(json.dumps({"success": False, "transcript": "", "method": "none", "duration_seconds": 0, "logs": ["audio_path é obrigatório"]}, ensure_ascii=False))
        sys.exit(1)

    result = transcribe_audio(audio_path)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
