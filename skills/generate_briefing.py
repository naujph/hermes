#!/usr/bin/env python3
"""Skill: generate_briefing

Gera briefing completo para reunião com lead/cliente.
Recebe JSON via stdin, retorna JSON via stdout.

Uso:
    echo '{"company_id": 1}' | python hermes/skills/generate_briefing.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import get_connection
from app.repositories.company_repository import CompanyRepository
from app.repositories.lead_repository import LeadRepository
from app.utils.normalizers import loads_json


def fetch_company_full(company_id: int) -> dict:
    """Retorna company + último ai_score + interações + oportunidades."""
    company_repo = CompanyRepository()
    lead_repo = LeadRepository()

    company = company_repo.get_by_id(company_id) or {}
    if not company:
        return {}

    with get_connection() as conn:
        # Último AI score
        ai = conn.execute(
            '''SELECT * FROM ai_scores WHERE company_id = ? ORDER BY generated_at DESC LIMIT 1''',
            (company_id,),
        ).fetchone()

        # Leads vinculados
        leads = [dict(r) for r in conn.execute(
            'SELECT * FROM leads WHERE company_id = ?', (company_id,)
        ).fetchall()]

        # Interações do lead mais recente
        interactions = []
        if leads:
            interactions = [dict(r) for r in conn.execute(
                'SELECT * FROM interactions WHERE lead_id = ? ORDER BY occurred_at DESC LIMIT 10',
                (leads[0]['id'],),
            ).fetchall()]

        # Oportunidades
        opps = [dict(r) for r in conn.execute(
            'SELECT * FROM opportunities WHERE company_id = ?', (company_id,)
        ).fetchall()]

    return {
        'company': company,
        'ai_score': dict(ai) if ai else {},
        'leads': leads,
        'interactions': interactions,
        'opportunities': opps,
    }


def build_briefing(data: dict) -> str:
    company = data.get('company', {})
    ai = data.get('ai_score', {})
    leads = data.get('leads', [])
    interactions = data.get('interactions', [])
    opps = data.get('opportunities', [])

    lines = []
    lines.append(f"# Briefing: {company.get('razao_social') or company.get('nome_fantasia')}")
    lines.append("")
    lines.append("## Dados da Empresa")
    lines.append(f"- **CNPJ:** {company.get('cnpj') or 'N/A'}")
    lines.append(f"- **CNAE:** {company.get('cnae_principal_descricao') or company.get('cnae_principal') or 'N/A'}")
    lines.append(f"- **Porte:** {company.get('porte') or 'N/A'}")
    lines.append(f"- **Situação:** {company.get('situacao_cadastral') or 'N/A'}")
    lines.append(f"- **Capital Social:** R$ {company.get('capital_social') or 'N/A'}")
    lines.append(f"- **Cidade:** {company.get('endereco_municipio') or 'N/A'} - {company.get('endereco_uf') or 'N/A'}")
    lines.append(f"- **Site:** {company.get('website') or 'N/A'}")
    lines.append(f"- **Telefone:** {company.get('telefone') or 'N/A'}")
    lines.append("")

    socios = loads_json(company.get('socios_json') or '[]')
    if socios:
        lines.append("## Sócios")
        for s in socios[:5]:
            lines.append(f"- {s.get('nome')} ({s.get('qualificacao')})")
        lines.append("")

    if ai:
        lines.append("## Análise de IA")
        lines.append(f"- **Score Potencial:** {ai.get('score_potencial')}")
        lines.append(f"- **Score Maturidade:** {ai.get('score_maturidade')}")
        lines.append(f"- **Score Acessibilidade:** {ai.get('score_acessibilidade')}")
        lines.append(f"- **Score Total:** {ai.get('score_total')}")
        lines.append("")
        if ai.get('resumo_executivo'):
            lines.append(f"**Resumo:** {ai.get('resumo_executivo')}")
        if ai.get('tese_abordagem'):
            lines.append(f"**Tese:** {ai.get('tese_abordagem')}")
        if ai.get('observacoes'):
            lines.append(f"**Observações:** {ai.get('observacoes')}")
        lines.append("")

    if opps:
        lines.append("## Oportunidades em Andamento")
        for o in opps:
            lines.append(f"- {o.get('title')} | Estágio: {o.get('stage')} | Valor: R$ {o.get('estimated_value') or 'N/A'}")
        lines.append("")

    if interactions:
        lines.append("## Histórico de Interações")
        for i in interactions[:5]:
            lines.append(f"- [{i.get('channel')}] {i.get('interaction_type')}: {i.get('message_text') or 'Sem texto'}")
        lines.append("")

    lines.append("## Pontos de Atenção")
    lines.append("- Verificar se há mudanças recentes na situação cadastral")
    lines.append("- Confirmar contato correto antes da reunião")
    if company.get('situacao_cadastral') and 'ativa' not in str(company.get('situacao_cadastral')).lower():
        lines.append("- ⚠️ EMPRESA NÃO ESTÁ ATIVA NA RECEITA FEDERAL")
    lines.append("")

    return "\n".join(lines)


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "errors": ["JSON inválido"]}))
        sys.exit(1)

    company_id = payload.get('company_id')
    if not company_id:
        print(json.dumps({"success": False, "errors": ["company_id é obrigatório"]}))
        sys.exit(1)

    data = fetch_company_full(company_id)
    if not data.get('company'):
        print(json.dumps({"success": False, "errors": [f"Company {company_id} não encontrada"]}))
        sys.exit(1)

    briefing = build_briefing(data)
    products = loads_json(data['ai_score'].get('produtos_sugeridos_json') or '[]') if data.get('ai_score') else []

    print(json.dumps({
        "success": True,
        "briefing": briefing,
        "products_suggested": products,
        "logs": [f"Briefing gerado para company_id={company_id}"],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
