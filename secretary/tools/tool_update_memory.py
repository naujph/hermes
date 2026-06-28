"""Tool: update_memory — Gerencia memória pessoal do Juan."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes.secretary.context.personal_memory import PersonalMemory


mem = PersonalMemory()


def update_memory(action: str, category: str, key: str, value: str, confidence: float = 0.9) -> dict:
    """Adiciona, atualiza ou remove fatos da memória pessoal."""
    action = action.lower()

    if action == "add":
        fact = mem.add_fact(category=category, key=key, value=value, confidence=confidence)
        return {"success": True, "message": f"Lembrei: [{category}] {key} = {value}", "fact_id": fact["id"]}

    elif action == "update":
        # Busca fato pela chave
        existing = mem.get_fact(key)
        if existing:
            mem.update_fact(existing["id"], value=value, confidence=confidence)
            return {"success": True, "message": f"Atualizado: {key} = {value}", "fact_id": existing["id"]}
        # Se não existe, adiciona
        fact = mem.add_fact(category=category, key=key, value=value, confidence=confidence)
        return {"success": True, "message": f"Novo fato adicionado: {key} = {value}", "fact_id": fact["id"]}

    elif action == "delete":
        existing = mem.get_fact(key)
        if existing:
            mem.delete_fact(existing["id"])
            return {"success": True, "message": f"Removido: {key}"}
        return {"success": False, "message": f"Fato '{key}' não encontrado para remover."}

    else:
        return {"success": False, "error": f"Ação '{action}' inválida. Use add, update ou delete."}
