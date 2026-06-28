"""Chat Simulator — Simula uma conversa WhatsApp no terminal.

Funciona 100% offline, sem WhatsApp. Permite testar o Hermes Secretary
antes do chip chegar.

Comandos especiais:
  /reset    — limpa histórico da sessão
  /memory   — mostra memória pessoal atual
  /snapshot — mostra snapshot do sistema
  /logs     — mostra últimas interações
  /quit     — sai
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes.secretary.core import HermesCore
from hermes.secretary.context.personal_memory import PersonalMemory


def print_banner():
    print("═" * 60)
    print("  🤖 HERMES SECRETARY — Simulador de Chat Offline")
    print("  Modo: TERMINAL (sem WhatsApp)")
    print("  Digite /help para comandos especiais")
    print("═" * 60)
    print()


def print_help():
    print("Comandos especiais:")
    print("  /reset     — limpa histórico da sessão")
    print("  /memory    — mostra fatos salvos na memória pessoal")
    print("  /snapshot  — mostra métricas do sistema")
    print("  /logs      — mostra histórico de conversa")
    print("  /help      — mostra esta ajuda")
    print("  /quit      — sai do simulador")
    print()


def main():
    print_banner()

    print("🔄 Iniciando Hermes Core...")
    try:
        core = HermesCore()
    except Exception as exc:
        print(f"❌ Erro ao iniciar HermesCore: {exc}")
        sys.exit(1)

    print("✅ Hermes pronto! Manda ver, Juan.\n")

    while True:
        try:
            user_input = input("📱 Juan: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 Até logo!")
            break

        if not user_input:
            continue

        # Comandos especiais
        if user_input.lower() == "/quit":
            print("👋 Até logo!")
            break
        elif user_input.lower() == "/help":
            print_help()
            continue
        elif user_input.lower() == "/reset":
            core.history.clear()
            print("🔄 Histórico limpo.\n")
            continue
        elif user_input.lower() == "/memory":
            mem = PersonalMemory()
            facts = mem.list_facts()
            if not facts:
                print("🧠 Memória vazia.\n")
            else:
                print("🧠 Memória pessoal:")
                for f in facts[-10:]:
                    print(f"   [{f['category']}] {f['key']}: {f['value']}")
                print()
            continue
        elif user_input.lower() == "/snapshot":
            snap = core._get_system_snapshot_text()
            print(f"📊 {snap}\n")
            continue
        elif user_input.lower() == "/logs":
            if not core.history:
                print("📄 Sem interações ainda.\n")
            else:
                print("📄 Últimas interações:")
                for entry in core.history[-5:]:
                    print(f"   Juan: {entry['user'][:60]}...")
                print()
            continue

        # Processa mensagem normal
        print("🤖 Hermes está pensando...")
        try:
            result = core.process_message(user_input)
        except Exception as exc:
            print(f"❌ Erro: {exc}\n")
            continue

        if result.get("success"):
            response = result.get("response", "...")
            tool_used = result.get("tool_used", "direct_response")
            print(f"🤖 Hermes: {response}")
            if tool_used != "direct_response":
                print(f"   (usou: {tool_used})")
        else:
            print(f"❌ Erro: {result.get('error', 'desconhecido')}")

        print()


if __name__ == "__main__":
    main()
