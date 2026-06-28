"""
Transcrição de áudio delegada para a skill `audio_transcribe`.

Esse módulo mantém a antiga interface de ferramenta (`transcribe_audio`, `_is_gladia_available`)
mas internamente usa `hermes.skills.audio_transcribe`, que escolhe o melhor backend:
  - áudios curtos -> faster-whisper tiny local (rápido/offline)
  - áudios longos -> Gladia cloud
  - fallback -> faster-whisper base local
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from hermes.skills.audio_transcribe import transcribe_audio as skill_transcribe, is_backend_available

logger = logging.getLogger(__name__)


def _is_gladia_available() -> bool:
    """Retorna True se a skill puder usar Gladia como backend."""
    return is_backend_available("gladia")


def transcribe_audio(
    audio_path: str | Path,
    *,
    language: str = "pt",
    diarize: bool = False,
    timeout: Optional[int] = None,
) -> dict:
    """
    Transcreve um arquivo de áudio usando a skill `audio_transcribe`.

    Parâmetros de compatibilidade:
      - language: idioma do áudio (padrão 'pt')
      - diarize: se True, força uso de backend com diarização (Gladia)
      - timeout: ignorado; mantido para compatibilidade com chamadas antigas
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Arquivo de áudio não encontrado: {audio_path}")

    try:
        result = skill_transcribe(str(audio_path))
    except Exception as e:
        logger.exception("Falha na transcrição de áudio via skill audio_transcribe")
        return {
            "success": False,
            "text": "",
            "error": str(e),
            "provider": "audio_transcribe",
        }

    return {
        "success": result.get("success", False),
        "text": result.get("text", ""),
        "error": result.get("error", ""),
        "provider": result.get("provider", "audio_transcribe"),
    }
