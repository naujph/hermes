"""Sistema de Memória Pessoal do Hermes Secretary.

Gerencia fatos, preferências, rotinas e contexto pessoal do Juan
em um arquivo JSON persistente. O Hermes pode ler e escrever
nessa memória para ficar mais inteligente com o tempo.

Versão 2 — memória rica com perfil estruturado, projetos, stack,
conversas e preferências. Write com RLock + debounce automático.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class PersonalMemory:
    """Gerenciador de memória pessoal persistente."""

    def __init__(self, memory_path: str | Path | None = None):
        if memory_path is None:
            self.memory_path = Path(__file__).resolve().parent / "memory_store.json"
        else:
            self.memory_path = Path(memory_path)
        self._data: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._load()

    # ── Internals ────────────────────────────────────────────────────

    def _load(self) -> None:
        """Carrega a memória do disco."""
        with self._lock:
            if self.memory_path.exists():
                try:
                    with open(self.memory_path, "r", encoding="utf-8") as f:
                        self._data = json.load(f)
                    self._ensure_defaults()
                except (json.JSONDecodeError, OSError) as exc:
                    print(f"[Memory] Erro ao carregar: {exc}. Iniciando vazio.")
                    self._data = self._default_data()
                    self._save_now()
            else:
                self._data = self._default_data()
                self._save_now()

    def _ensure_defaults(self) -> None:
        """Garante que chaves obrigatórias do schema v2 existam (compat com arquivos antigos)."""
        defaults = self._default_data()
        for key, value in defaults.items():
            self._data.setdefault(key, value)

    def _save_now(self) -> None:
        """Write síncrono para disco."""
        with self._lock:
            self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.memory_path.with_suffix(".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                tmp.replace(self.memory_path)
                self._dirty = False
            except OSError as exc:
                print(f"[Memory] Erro ao salvar: {exc}")

    def _debounced_save(self) -> None:
        """Agenda um write automático após 2s sem alterações."""
        def _auto():
            time.sleep(2.0)
            with self._lock:
                if not self._dirty:
                    return
            self._save_now()

        threading.Thread(target=_auto, daemon=True).start()

    def _touch(self) -> None:
        """Marca como sujo e dispara auto-save."""
        with self._lock:
            self._dirty = True
        self._debounced_save()

    # ── Data model ─────────────────────────────────────────────────────

    def _default_data(self) -> dict[str, Any]:
        return {
            "version": 2,
            "owner": "Juan",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "profile": {},
            "projects": [],
            "tech_stack": {},
            "ambiente": {},
            "comandos_hermes": {},
            "conversas": [],
            "preferencias": {},
            "historico_windows": {},
            "facts": [],           # v1 compat
            "contacts_important": [],
            "routines": [],
        }

    # ── Generic get/set ──────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
        self._touch()

    # ── Profile (v2 rich) ────────────────────────────────────────────

    def update_profile(self, **fields: Any) -> None:
        """Atualiza campos do perfil de forma incremental."""
        with self._lock:
            profile = self._data.setdefault("profile", {})
            profile.update(fields)
        self._touch()

    def get_profile_text(self) -> str:
        """Retorna o perfil formatado para o prompt do LLM."""
        with self._lock:
            profile = self._data.get("profile", {})
            if not profile:
                return ""
            lines = ["### PERFIL DO JUAN"]
            for k, v in profile.items():
                if isinstance(v, list):
                    lines.append(f"{k}: {', '.join(str(x) for x in v)}")
                else:
                    lines.append(f"{k}: {v}")
            return "\n".join(lines)

    # ── Conversations ────────────────────────────────────────────────

    def add_conversation(self, resumo: str, topicos: List[str]) -> None:
        """Adiciona uma nova entrada ao histórico de conversas."""
        with self._lock:
            conversas = self._data.setdefault("conversas", [])
            conversas.append(
                {
                    "data": datetime.now(timezone.utc).isoformat(),
                    "resumo": resumo,
                    "topicos": topicos,
                }
            )
            self._data["conversas"] = conversas[-50:]  # manter últimas 50
        self._touch()

    def get_last_conversations(self, n: int = 3) -> List[Dict[str, Any]]:
        with self._lock:
            return self._data.get("conversas", [])[-n:]

    # ── Projects ─────────────────────────────────────────────────────

    def add_project(self, project: Dict[str, Any]) -> None:
        """Adiciona ou atualiza um projeto pelo nome."""
        with self._lock:
            projects = self._data.setdefault("projects", [])
            for idx, p in enumerate(projects):
                if p.get("nome") == project.get("nome"):
                    projects[idx] = {**p, **project}
                    break
            else:
                projects.append(project)
        self._touch()

    def get_projects_text(self) -> str:
        with self._lock:
            projects = self._data.get("projects", [])
            if not projects:
                return ""
            lines = ["### PROJETOS ATIVOS"]
            for p in projects:
                lines.append(f"\n{p.get('nome', 'Sem nome')} — {p.get('tipo', '')}")
                for k, v in p.items():
                    if k not in ("nome", "tipo"):
                        if isinstance(v, list):
                            lines.append(f"  {k}: {', '.join(str(x) for x in v)}")
                        else:
                            lines.append(f"  {k}: {v}")
            return "\n".join(lines)

    # ── Preferences ──────────────────────────────────────────────────

    def update_preferences(self, **fields: Any) -> None:
        with self._lock:
            pref = self._data.setdefault("preferencias", {})
            pref.update(fields)
        self._touch()

    def get_preferences_text(self) -> str:
        with self._lock:
            prefs = self._data.get("preferencias", {})
            if not prefs:
                return ""
            lines = ["### PREFERÊNCIAS DO JUAN"]
            for k, v in prefs.items():
                lines.append(f"  {k}: {v}")
            return "\n".join(lines)

    # ── v1 Fact management (mantido para compat) ────────────────────

    def add_fact(self, category: str, key: str, value: str, confidence: float = 1.0, source: str = "hermes") -> dict[str, Any]:
        fact = {
            "id": f"fact_{uuid.uuid4().hex[:8]}",
            "category": category,
            "key": key,
            "value": value,
            "confidence": confidence,
            "source": source,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            for existing in self._data["facts"]:
                if existing["key"] == key and existing["value"] == value:
                    existing["confidence"] = max(existing["confidence"], confidence)
                    existing["source"] = source
                    self._touch()
                    return existing
            self._data["facts"].append(fact)
        self._touch()
        return fact

    def search_facts(self, query: str, category: str | None = None) -> list[dict[str, Any]]:
        q = query.lower()
        with self._lock:
            results = []
            for fact in self._data.get("facts", []):
                if category and fact["category"] != category:
                    continue
                if q in fact["key"].lower() or q in str(fact["value"]).lower():
                    results.append(fact)
            return sorted(results, key=lambda x: x["confidence"], reverse=True)

    def list_facts(self, category: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Lista todos os fatos pessoais, opcionalmente filtrados por categoria."""
        with self._lock:
            facts = self._data.get("facts", [])
            if category:
                facts = [f for f in facts if f.get("category") == category]
            return sorted(facts, key=lambda x: x.get("confidence", 1.0), reverse=True)[:limit]

    # ── Contacts / Routines (v1 compat) ────────────────────────────────

    def add_contact(self, name: str, phone: str | None = None, email: str | None = None, notes: str = "") -> dict[str, Any]:
        contact = {
            "id": f"contact_{uuid.uuid4().hex[:8]}",
            "name": name,
            "phone": phone,
            "email": email,
            "notes": notes,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._data.setdefault("contacts_important", []).append(contact)
        self._touch()
        return contact

    def add_routine(self, description: str, frequency: str, time_of_day: str | None = None) -> dict[str, Any]:
        routine = {
            "id": f"routine_{uuid.uuid4().hex[:8]}",
            "description": description,
            "frequency": frequency,
            "time_of_day": time_of_day,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._data.setdefault("routines", []).append(routine)
        self._touch()
        return routine

    # ── Context builder ──────────────────────────────────────────────

    def build_context_block(self, max_facts: int = 15) -> str:
        """Monta um bloco de texto com contexto pessoal para injetar no prompt do LLM."""
        with self._lock:
            lines: list[str] = ["### CONTEXTO PESSOAL DO JUAN (memória persistente)", ""]

            profile = self._data.get("profile", {})
            if profile:
                lines.append("**Perfil:**")
                for k, v in profile.items():
                    if isinstance(v, list):
                        lines.append(f"  - {k}: {', '.join(str(x) for x in v)}")
                    else:
                        lines.append(f"  - {k}: {v}")
                lines.append("")

            projects = self._data.get("projects", [])
            if projects:
                lines.append("**Projetos ativos:**")
                for p in projects[:5]:
                    lines.append(f"  - {p.get('nome', 'Sem nome')} ({p.get('status', 'ativo')})")
                    desc = p.get("descricao", "")
                    if desc:
                        lines.append(f"    {desc}")
                lines.append("")

            prefs = self._data.get("preferencias", {})
            if prefs:
                lines.append("**Preferências:**")
                for k, v in prefs.items():
                    lines.append(f"  - {k}: {v}")
                lines.append("")

            conversas = self._data.get("conversas", [])
            if conversas:
                lines.append("**Últimas conversas:**")
                for c in conversas[-3:]:
                    lines.append(f"  [{c.get('data', '?')[:10]}] {c.get('resumo', '')}")
                lines.append("")

            facts = sorted(
                self._data.get("facts", []), key=lambda x: x.get("confidence", 1.0), reverse=True
            )[:max_facts]
            if facts:
                lines.append("**Fatos importantes:**")
                for f in facts:
                    lines.append(f"  - [{f['category']}] {f['key']}: {f['value']}")
                lines.append("")

            return "\n".join(lines)

    def to_context_string(self) -> str:
        """Alias para build_context_block."""
        return self.build_context_block()

    def dump(self) -> str:
        """Retorna o JSON inteiro formatado (para debug)."""
        with self._lock:
            return json.dumps(self._data, ensure_ascii=False, indent=2)


# ── Singleton global ───────────────────────────────────────────────

_memory_instance: PersonalMemory | None = None


def get_memory() -> PersonalMemory:
    """Retorna a instância singleton de PersonalMemory."""
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = PersonalMemory()
    return _memory_instance
