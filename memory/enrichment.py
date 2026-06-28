"""Enrichment — Revisão recorrente de nós isolados.

Toda semana, passa nós com poucas arestas pelo LLM e pergunta:
"Com base no que sabemos sobre Juan, que relações faltam para este nó?"
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any, Dict, List

from hermes.memory.graph_manager import GraphManager
from hermes.memory.inference_engine import InferenceEngine


class Enrichment:
    """Revisa nós isolados e propõe links faltantes."""

    def __init__(self, graph: GraphManager | None = None,
                 inference: InferenceEngine | None = None):
        self.graph = graph or GraphManager()
        self.inference = inference or InferenceEngine(graph=graph)

    def find_isolated_nodes(self, max_degree: int = 1, limit: int = 10) -> List[Dict[str, Any]]:
        """Retorna nós com poucas conexões."""
        isolated = []
        for nid, data in self.graph._graph.nodes(data=True):
            degree = self.graph._graph.degree(nid)
            if degree <= max_degree:
                isolated.append({
                    "id": nid,
                    "label": data.get("label", nid),
                    "type": data.get("type", "?"),
                    "degree": degree,
                    "properties": data.get("properties", {}),
                })
        # Prioriza pessoas e projetos
        priority = {"Person": 0, "Project": 1, "Organization": 2}
        isolated.sort(key=lambda x: priority.get(x["type"], 99))
        return isolated[:limit]

    def enrich_node(self, node_id: str) -> int:
        """Pergunta ao LLM que relações faltam para um nó isolado.

        Retorna número de arestas criadas.
        """
        node_data_list = self.graph.find_node(node_id=node_id)
        if not node_data_list:
            return 0
        _, attrs = node_data_list[0]

        label = attrs.get("label", node_id)
        type_ = attrs.get("type", "?")
        props = attrs.get("properties", {})

        # Contexto do nó + vizinhos atuais
        neighbors = self.graph.get_neighbors(node_id, hops=1)
        neighbor_text = "\n".join(
            f"  - {self.graph.find_node(node_id=nid)[0][1].get('label', nid)} (relação: {rel})"
            for nid, rel, _ in neighbors
        ) if neighbors else "  (nenhuma conexão)"

        prompt = (
            f"Você é o sistema de memória do Hermes.\n"
            f"Analisamos o seguinte nó isolado no grafo de conhecimento:\n\n"
            f"Nó: {label} (tipo: {type_})\n"
            f"Propriedades: {json.dumps(props, ensure_ascii=False)}\n"
            f"Conexões atuais:\n{neighbor_text}\n\n"
            f"Com base no perfil do Juan (assessor de investimentos, 24 anos, Palmas-PR, \n"
            f"esposa Alianely, projetos Acumen Score e Lead Prospecting Engine), \n"
            f"sugira NOVAS relações que este nó deveria ter.\n\n"
            f"Responda APENAS com JSON array:\n"
            f'[{{"target": "nome_entidade", "relation": "married_to", "reason": "breve"}}]\n'
            f"JSON:"
        )

        try:
            raw = self.inference._call_llm(prompt)
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if not match:
                return 0
            parsed = json.loads(match.group(0))
        except Exception:
            return 0

        created = 0
        for item in parsed:
            target_label = item.get("target", "").strip()
            relation = item.get("relation", "").strip().lower()
            reason = item.get("reason", "")

            if not target_label or not relation:
                continue

            # Normaliza relação
            relation = self.inference._normalize_relation(relation)
            if relation not in InferenceEngine.ALLOWED_RELATIONS:
                continue

            # Resolve target
            target_nodes = self.graph.find_node(label=target_label)
            if not target_nodes:
                # Cria nó se não existir (tipo genérico)
                target_id = self.graph.add_node(target_label, "Concept", properties={"auto_created": True})
            else:
                target_id = target_nodes[0][0]

            # Cria aresta
            if not self.graph._graph.has_edge(node_id, target_id):
                self.graph.add_edge(
                    node_id, target_id,
                    relation=relation,
                    properties={"inferred_by": "enrichment", "reason": reason},
                    confidence=0.70,
                )
                created += 1

        return created

    def run(self) -> Dict[str, int]:
        """Roda enriquecimento em todos os nós isolados."""
        isolated = self.find_isolated_nodes()
        total_created = 0

        for node in isolated:
            created = self.enrich_node(node["id"])
            total_created += created

        return {
            "isolated_nodes_checked": len(isolated),
            "new_edges_created": total_created,
        }
