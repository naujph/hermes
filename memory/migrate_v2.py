"""migrate_v2.py — Migra memory_store.json (v2) para grafo + vector store.

Roda uma vez. Popula nós, arestas e chunks a partir do JSON legado.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes.memory.graph_manager import GraphManager
from hermes.memory.ingestor import Ingestor


def migrate():
    json_path = Path(__file__).resolve().parent / ".." / "secretary" / "context" / "memory_store.json"
    json_path = json_path.resolve()

    if not json_path.exists():
        print(f"[MIGRATE] Nao encontrado: {json_path}")
        sys.exit(1)

    print(f"[MIGRATE] Carregando {json_path}...")
    data = json.loads(json_path.read_text(encoding="utf-8"))

    graph = GraphManager()
    ingestor = Ingestor(graph=graph)

    # ── 1. Profile → nós + chunks ──────────────────────────────────
    profile = data.get("profile", {})
    if profile:
        print("[MIGRATE] Migrando profile...")
        juan_id = graph.add_node("Juan", "Person", properties={"role": "owner"})

        for key, value in profile.items():
            if isinstance(value, list):
                val_str = ", ".join(str(v) for v in value)
            else:
                val_str = str(value)

            # Cria chunk no vector store
            text = f"Juan {key}: {val_str}"
            ingestor.ingest(text, source="profile", source_ref=f"profile.{key}",
                            node_links=[juan_id])

            # Alguns campos viram nós relacionados
            if key == "familia":
                # Tenta extrair pessoas/pets
                if "Alianely" in val_str:
                    alianely_id = graph.add_node("Alianely", "Person", properties={"occupation": "audiovisual"})
                    graph.add_edge(juan_id, alianely_id, "married_to")
                if "Chico" in val_str:
                    chico_id = graph.add_node("Chico", "Pet", properties={"type": "cachorro"})
                    graph.add_edge(juan_id, chico_id, "has_pet")
                if "Charlotte" in val_str:
                    charlotte_id = graph.add_node("Charlotte", "Pet", properties={"type": "cachorro"})
                    graph.add_edge(juan_id, charlotte_id, "has_pet")

            if key == "empresa" and "A1" in val_str:
                a1_id = graph.add_node("A1 Investimentos", "Organization")
                xp_id = graph.add_node("XP Inc", "Organization")
                graph.add_edge(juan_id, a1_id, "works_at")
                graph.add_edge(a1_id, xp_id, "subsidiary_of")

            if key == "cargo":
                graph._graph.nodes[juan_id]["properties"]["role"] = val_str
                graph._save_node(juan_id)

    # ── 2. Projects → nós + chunks ───────────────────────────────────
    projects = data.get("projects", [])
    for proj in projects:
        print(f"[MIGRATE] Migrando projeto: {proj.get('nome')}...")
        proj_name = proj.get("nome", "Projeto")
        proj_id = graph.add_node(proj_name, "Project", properties={
            "type": proj.get("tipo", ""),
            "status": "ativo",
        })
        graph.add_edge(juan_id, proj_id, "owns_project")

        # Descrição
        desc = proj.get("descricao", "")
        if desc:
            ingestor.ingest(f"{proj_name}: {desc}", source="project",
                            source_ref=f"project.{proj_name}", node_links=[proj_id])

        # Stack
        stack = proj.get("stack", [])
        for tech in stack:
            tech_id = graph.add_node(tech, "Technology")
            graph.add_edge(proj_id, tech_id, "uses_tech")

        # Regras
        rules = proj.get("regras_de_ouro", [])
        for rule in rules:
            ingestor.ingest(f"Regra do {proj_name}: {rule}", source="project",
                            source_ref=f"project.{proj_name}.rules", node_links=[proj_id])

    # ── 3. Conversations → chunks ──────────────────────────────────
    conversas = data.get("conversas", [])
    for c in conversas:
        resumo = c.get("resumo", "")
        if resumo:
            ingestor.ingest(resumo, source="conversation", source_ref=f"chat.{c.get('data', '')}")

    # ── 4. Tech stack → nós ──────────────────────────────────────────
    tech_stack = data.get("tech_stack", {})
    for category, details in tech_stack.items():
        if isinstance(details, dict):
            for k, v in details.items():
                if isinstance(v, str):
                    text = f"{category} {k}: {v}"
                    ingestor.ingest(text, source="tech_stack", source_ref=f"tech.{category}")

    # ── 5. Preferências → nós + chunks ─────────────────────────────
    prefs = data.get("preferencias", {})
    for k, v in prefs.items():
        text = f"Preferencia do Juan: {k} = {v}"
        ingestor.ingest(text, source="preference", source_ref=f"pref.{k}", node_links=[juan_id])

    # ── 6. Ambiente → chunks ───────────────────────────────────────
    ambiente = data.get("ambiente", {})
    for k, v in ambiente.items():
        text = f"Ambiente: {k} = {v}"
        ingestor.ingest(text, source="environment", source_ref=f"env.{k}")

    # ── 7. Facts (v1 compat) → chunks ────────────────────────────────
    facts = data.get("facts", [])
    for f in facts:
        text = f"[{f.get('category', 'geral')}] {f.get('key', '')}: {f.get('value', '')}"
        ingestor.ingest(text, source="fact", source_ref=f"fact.{f.get('id', '')}")

    # ── Stats ────────────────────────────────────────────────────────
    print("\n[MIGRATE] Concluido!")
    print(json.dumps(ingestor.stats(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    migrate()
