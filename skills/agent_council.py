"""Agent Council — Coleta opiniones de multiplos AIs e sintetiza uma resposta.

Adaptacao Python do agent-council para o Hermes.
Consulta membros em paralelo e o proprio Hermes (chairman) sintetiza.

Como usar:
    from hermes.skills.agent_council import AgentCouncil
    council = AgentCouncil()
    result = council.ask("Devo investir em BTC agora?")

Configuracao:
    Edite council.config.yaml na raiz do projeto.
"""
from __future__ import annotations

import json
import os
import sys
import concurrent.futures
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Garante UTF-8 no stdout no Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from app.llm_client import UnifiedLLMClient

# --- Config default ---
DEFAULT_CONFIG = {
    "council": {
        "chairman": {"role": "auto"},
        "members": [
            {
                "name": "kimi",
                "provider": "ollama",
                "model": "kimi-k2:1t",
                "emoji": "🧠",
                "description": "Ollama Cloud - kimi-k2.6 equivalente",
            },
            {
                "name": "gemini",
                "provider": "gemini",
                "model": "gemini-2.5-pro",
                "emoji": "💎",
                "description": "Google Gemini (se API key disponivel)",
            },
            {
                "name": "claude",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "emoji": "🎯",
                "description": "Claude Sonnet (se API key disponivel)",
            },
        ],
        "settings": {
            "exclude_chairman_from_members": True,
            "timeout": 120,
            "max_tokens": 2048,
        },
    }
}


def _load_config() -> dict:
    config_path = ROOT / "council.config.yaml"
    if config_path.exists():
        try:
            import yaml
            return yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return DEFAULT_CONFIG


def _get_api_client(provider: str):
    """Retorna funcao de chamada para cada provider."""
    if provider == "ollama":
        return _call_ollama
    if provider == "gemini":
        return _call_gemini
    if provider == "anthropic":
        return _call_anthropic
    return _call_dummy


def _call_ollama(prompt: str, model: str, timeout: int = 60) -> str:
    """Usa cliente LLM unificado (Ollama Cloud / OpenAI-compatible)."""
    try:
        client = UnifiedLLMClient(provider="openai", model=model, timeout=timeout)
        resp = client.complete(prompt, temperature=0.7, max_tokens=2048)
        if resp.error:
            return f"[ERRO Ollama: {resp.error}]"
        return resp.content
    except Exception as exc:
        return f"[ERRO Ollama: {exc}]"


def _call_gemini(prompt: str, model: str, timeout: int = 60) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return "[ERRO: GEMINI_API_KEY nao configurada]"
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        m = genai.GenerativeModel(model_name=model)
        resp = m.generate_content(prompt)
        return resp.text
    except Exception as exc:
        return f"[ERRO Gemini: {exc}]"


def _call_anthropic(prompt: str, model: str, timeout: int = 60) -> str:
    """Usa cliente LLM unificado (Anthropic)."""
    try:
        client = UnifiedLLMClient(provider="anthropic", model=model, timeout=timeout)
        resp = client.complete(prompt, temperature=0.7, max_tokens=2048)
        if resp.error:
            return f"[ERRO Claude: {resp.error}]"
        return resp.content
    except Exception as exc:
        return f"[ERRO Claude: {exc}]"


def _call_dummy(prompt: str, model: str, timeout: int = 60) -> str:
    return "[Membro nao configurado]"


class AgentCouncil:
    """Motor do Agent Council para Hermes."""

    def __init__(self, config: dict | None = None):
        self.config = config or _load_config()
        self.settings = self.config.get("council", {}).get("settings", {})
        self.members = self.config.get("council", {}).get("members", [])
        self.chairman = self.config.get("council", {}).get("chairman", {"role": "auto"})
        self.timeout = self.settings.get("timeout", 120)

    def ask(self, question: str, context: str = "") -> dict:
        """Consulta todos os membros em paralelo e retorna resultado estruturado."""
        job_id = str(uuid.uuid4())[:8]
        print(f"[COUNCIL] Iniciando sessao {job_id}", file=sys.stderr)
        print(f"[COUNCIL] Pergunta: {question[:100]}...", file=sys.stderr)
        print(f"[COUNCIL] Membros: {len(self.members)}", file=sys.stderr)

        prompt = question
        if context:
            prompt = f"Contexto:\n{context}\n\nPergunta: {question}"

        # Consulta membros em paralelo
        responses = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.members)) as executor:
            futures = {}
            for member in self.members:
                name = member["name"]
                provider = member.get("provider", "ollama")
                model = member.get("model", "kimi-k2:1t")
                fn = _get_api_client(provider)
                future = executor.submit(fn, prompt, model, self.timeout)
                futures[future] = member

            for future in concurrent.futures.as_completed(futures):
                member = futures[future]
                name = member["name"]
                emoji = member.get("emoji", "🤖")
                try:
                    text = future.result(timeout=self.timeout)
                    responses[name] = {"member": name, "emoji": emoji, "response": text, "ok": True}
                    print(f"[COUNCIL] {emoji} {name}: OK ({len(text)} chars)", file=sys.stderr)
                except Exception as exc:
                    responses[name] = {"member": name, "emoji": emoji, "response": str(exc), "ok": False}
                    print(f"[COUNCIL] {emoji} {name}: ERRO - {exc}", file=sys.stderr)

        # Monta resultado para sintese do chairman
        result = {
            "job_id": job_id,
            "question": question,
            "timestamp": datetime.now().isoformat(),
            "members": list(responses.values()),
            "synthesis_ready": True,
        }

        # Salva log
        log_path = ROOT / "hermes" / "council_logs"
        log_path.mkdir(parents=True, exist_ok=True)
        log_file = log_path / f"council_{job_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        log_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[COUNCIL] Log salvo: {log_file}", file=sys.stderr)

        return result

    def format_for_synthesis(self, result: dict) -> str:
        """Formata as respostas dos membros para o chairman sintetizar."""
        lines = [
            "=== AGENT COUNCIL ===",
            f"Pergunta: {result['question']}",
            f"Membros consultados: {len(result['members'])}",
            "",
        ]
        for m in result["members"]:
            emoji = m.get("emoji", "🤖")
            name = m["member"]
            resp = m.get("response", "")
            lines.append(f"--- {emoji} {name.upper()} ---")
            lines.append(resp[:2000])
            lines.append("")
        lines.append("=== FIM DOS MEMBROS ===")
        lines.append("")
        lines.append("Como chairman, analise todas as opiniones acima e forneca uma sintese final com:")
        lines.append("1. Pontos de concordancia entre os membros")
        lines.append("2. Divergencias importantes")
        lines.append("3. Recomendacao final baseada no consenso")
        return "\n".join(lines)


# --- CLI rapido para teste ---
if __name__ == "__main__":
    question = ""

    # Suporta entrada JSON via stdin (usado pelo dispatcher run_skill)
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.buffer.read().decode("utf-8")
            payload = json.loads(raw) if raw.strip() else {}
            question = payload.get("question", "")
        except Exception:
            question = ""

    # Fallback para argumentos de linha de comando (backward compat)
    if not question and len(sys.argv) >= 2:
        question = " ".join(sys.argv[1:])

    if not question:
        print(json.dumps({
            "success": False,
            "error": "Uso: python hermes/skills/agent_council.py 'sua pergunta aqui' ou envie JSON via stdin com {\"question\": \"...\"}"
        }, ensure_ascii=False))
        sys.exit(1)

    council = AgentCouncil()
    result = council.ask(question)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
