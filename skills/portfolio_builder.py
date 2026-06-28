#!/usr/bin/env python3
"""Skill: portfolio_builder — Análise qualitativa de carteira pessoal do assessor.

Recebe JSON via stdin, retorna JSON via stdout.

Uso:
    echo '{"carteira": [
        {"ticker": "PETR4", "qtd": 100, "pm": 28.50, "setor": "Petróleo", "notas": "Divulgou resultado forte Q1"},
        {"ticker": "WEGE3", "qtd": 50, "pm": 35.00, "setor": "Indústria", "notas": ""}
    ]}' | python hermes/skills/portfolio_builder.py

    echo '{"carteira": [...], "acao": "rebalancear"}'  → sugere rebalanceamento
    echo '{"carteira": [...], "acao": "teses"}'        → gera teses por posição
    echo '{"carteira": [...], "acao": "alertas"}'        → detecta concentração excessiva
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Garante UTF-8 no stdout no Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from app.llm_client import UnifiedLLMClient

_llm = UnifiedLLMClient()


def _call_llm(prompt: str) -> str:
    """Chama LLM via cliente unificado."""
    resp = _llm.complete(prompt, temperature=0.2, max_tokens=2048)
    if resp.error:
        return f"[Erro LLM: {resp.error}]"
    return resp.content


def _build_carteira_text(carteira: list[dict]) -> str:
    lines = ["| Ticker | Qtd | PM (R$) | Setor | Notas do assessor |"]
    lines.append("|--------|-----|---------|-------|-------------------|")
    total_val = sum(p.get("qtd", 0) * p.get("pm", 0) for p in carteira)
    for p in carteira:
        ticker = p.get("ticker", "—")
        qtd = p.get("qtd", 0)
        pm = p.get("pm", 0)
        val = qtd * pm
        pct = (val / total_val * 100) if total_val else 0
        setor = p.get("setor", "—")
        notas = p.get("notas", "")
        lines.append(f"| {ticker} | {qtd} | {pm:.2f} | {setor} | {notas[:40]} | → R$ {val:,.2f} ({pct:.1f}%)")
    lines.append(f"\n**Patrimônio informado:** R$ {total_val:,.2f}")
    return "\n".join(lines)


def analyze_concentration(carteira: list[dict]) -> dict:
    """Detecta concentração por ativo e setor."""
    total = sum(p.get("qtd", 0) * p.get("pm", 0) for p in carteira)
    if total == 0:
        return {"success": False, "error": "Carteira vazia ou sem valores."}

    alerts = []
    by_ticker = {}
    by_setor = {}
    for p in carteira:
        val = p.get("qtd", 0) * p.get("pm", 0)
        by_ticker[p.get("ticker", "")] = by_ticker.get(p.get("ticker", ""), 0) + val
        by_setor[p.get("setor", "Diversos")] = by_setor.get(p.get("setor", "Diversos"), 0) + val

    for ticker, val in by_ticker.items():
        pct = val / total * 100
        if pct > 15:
            alerts.append(f"⚠️ {ticker} concentra {pct:.1f}% da carteira (limite sugerido: 15%)")

    for setor, val in by_setor.items():
        pct = val / total * 100
        if pct > 30:
            alerts.append(f"⚠️ Setor '{setor}' concentra {pct:.1f}% da carteira (limite sugerido: 30%)")

    # Sugere aportes/proporções usando LLM
    prompt = (
        "Você é assessor de investimentos experiente. Analise a carteira abaixo e sugira:\n"
        "1. Qual ativo/setor precisa de aporte ou redução\n"
        "2. Sugestão de % ideal de alocação por classe/setor\n"
        "3. Riscos de concentração visíveis\n\n"
        + _build_carteira_text(carteira)
        + "\n\nResponda em markdown, de forma direta e útil."
    )
    analysis = _call_llm(prompt)

    return {
        "success": True,
        "alerts": alerts,
        "analysis": analysis,
        "total": total,
        "by_ticker": {k: round(v, 2) for k, v in by_ticker.items()},
        "by_setor": {k: round(v, 2) for k, v in by_setor.items()},
    }


def generate_theses(carteira: list[dict]) -> dict:
    """Gera teses de investimento para cada posição."""
    prompt = (
        "Você é assessor de investimentos. Para cada posição da carteira abaixo, escreva:\n"
        "- Tese resumida (por que está na carteira?)\n"
        "- Catalisadores próximos (resultados, dividendos, eventos setoriais)\n"
        "- Riscos principais\n"
        "- Próximo passo de acompanhamento\n\n"
        "Use as notas do assessor como contexto.\n\n"
        + _build_carteira_text(carteira)
        + "\n\nResponda em markdown com uma seção por ticker."
    )
    theses = _call_llm(prompt)
    return {"success": True, "theses": theses}


def suggest_rebalance(carteira: list[dict]) -> dict:
    """Sugere rebalanceamento baseado em concentração e teses."""
    total = sum(p.get("qtd", 0) * p.get("pm", 0) for p in carteira)
    if total == 0:
        return {"success": False, "error": "Carteira vazia."}

    prompt = (
        "Você é assessor de investimentos. Com base na carteira abaixo, sugira um plano de rebalanceamento:\n"
        "1. O que vender (concentração excessiva, tese esgotada)\n"
        "2. O que manter\n"
        "3. O que comprar/aportar (setores sub-alocados, novas teses)\n"
        "4. Alocação sugerida pós-rebalanceamento (% por setor/classe)\n\n"
        "Considere as notas do assessor como parte da análise qualitativa.\n\n"
        + _build_carteira_text(carteira)
        + "\n\nResponda em markdown, direto e aplicável."
    )
    plan = _call_llm(prompt)
    return {"success": True, "rebalance_plan": plan}


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "errors": ["JSON inválido"]}))
        sys.exit(1)

    carteira = payload.get("carteira", [])
    acao = payload.get("acao", "alertas")

    if not carteira:
        print(json.dumps({"success": False, "errors": ["Informe a carteira."]}))
        sys.exit(1)

    if acao == "alertas" or acao == "concentracao":
        result = analyze_concentration(carteira)
    elif acao == "teses":
        result = generate_theses(carteira)
    elif acao == "rebalancear":
        result = suggest_rebalance(carteira)
    else:
        result = analyze_concentration(carteira)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
