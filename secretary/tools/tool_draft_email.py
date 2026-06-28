"""Tool: draft_email — Cria rascunhos de e-mail e os salva."""
from __future__ import annotations

import os
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DRAFTS_DIR = ROOT / "drafts"

def execute_draft_email(recipient: str, subject: str, body: str) -> dict[str, Any]:
    if not recipient or not subject or not body:
        return {"success": False, "error": "Destinatário, assunto e corpo são obrigatórios."}
        
    try:
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        safe_recipient = "".join([c for c in recipient if c.isalpha() or c.isdigit() or c==' ']).rstrip().replace(" ", "_").lower()
        if not safe_recipient:
            safe_recipient = "unknown"
            
        filename = f"draft_{timestamp}_{safe_recipient}.txt"
        filepath = DRAFTS_DIR / filename
        
        content = (
            f"PARA: {recipient}\n"
            f"ASSUNTO: {subject}\n"
            f"DATA DO RASCUNHO: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{'='*50}\n\n"
            f"{body}\n"
        )
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
            
        # Retorna sucesso e o preview
        return {
            "success": True, 
            "message": "Rascunho de e-mail criado com sucesso.",
            "path": str(filepath.relative_to(ROOT.parent)),
            "preview": content[:300] + "..."
        }
        
    except Exception as e:
        return {"success": False, "error": f"Erro ao salvar o rascunho: {str(e)}"}
