"""Retriever — Recuperação semântica com expansão de grafo.

Pipeline: query → embedding → k-NN no vector store → expande grafo (2-3 hops)
→ re-ranking → monta contexto para o LLM.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from hermes.memory.graph_manager import GraphManager
from hermes.memory.vector_store import VectorStore


class Retriever:
    """Busca inteligente que combina semântica + grafo."""

    def __init__(self, graph: GraphManager | None = None,
                 vector_store: VectorStore | None = None):
        self.graph = graph or GraphManager()
        self.vector = vector_store or VectorStore()

    # ── Core retrieval ───────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 5,
                 expand_hops: int = 2,
                 min_confidence: float = 0.5) -> Dict[str, Any]:
        """Recupera contexto rico para uma query.

        Retorna:
            {
                "chunks": [...],           # chunks relevantes (texto + metadata)
                "entities": [...],         # entidades mencionadas
                "subgraph": str,           # texto do subgrafo expandido
                "related_nodes": [...],    # nós do grafo trazidos por expansão
            }
        """
        # 1. Busca semântica
        chunks = self.vector.query(query, n_results=top_k * 2)
        if not chunks:
            return {
                "chunks": [],
                "entities": [],
                "subgraph": "",
                "related_nodes": [],
            }

        # 2. Coleta entidades dos chunks
        entity_ids = set()
        for chunk in chunks[:top_k]:
            for nid in chunk.get("node_ids", []):
                entity_ids.add(nid)

        # 3. Expansão de grafo (2-3 hops)
        expanded_nodes = []
        subgraph_lines = []
        visited = set(entity_ids)

        for nid in list(entity_ids)[:5]:  # limita para não explodir
            node_data = self.graph.find_node(node_id=nid)
            if not node_data:
                continue
            _, attrs = node_data[0]
            subgraph_lines.append(f"- {attrs.get('label', nid)} ({attrs.get('type', '?')})")

            # Expande hops
            for neighbor_id, relation, edge_data in self.graph.get_neighbors(nid, hops=expand_hops):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                neighbor_data = self.graph.find_node(node_id=neighbor_id)
                if neighbor_data:
                    _, nattrs = neighbor_data[0]
                    expanded_nodes.append({
                        "id": neighbor_id,
                        "label": nattrs.get("label", neighbor_id),
                        "type": nattrs.get("type", "?"),
                        "relation": relation,
                    })
                    subgraph_lines.append(
                        f"  → [{relation}] {nattrs.get('label', neighbor_id)} ({nattrs.get('type', '?')})"
                    )

        # 4. Re-ranking: promove chunks com entidades expandidas
        expanded_ids = {n["id"] for n in expanded_nodes}
        for chunk in chunks:
            boost = sum(1 for nid in chunk.get("node_ids", []) if nid in expanded_ids)
            chunk["score"] = chunk.get("distance", 1.0) - (boost * 0.1)

        chunks.sort(key=lambda x: x.get("score", x.get("distance", 1.0)))

        # 5. Coleta nomes de entidades
        entity_names = []
        for nid in visited:
            nd = self.graph.find_node(node_id=nid)
            if nd:
                entity_names.append(nd[0][1].get("label", nid))

        return {
            "chunks": chunks[:top_k],
            "entities": list(set(entity_names)),
            "subgraph": "### Grafo de conhecimento\n" + "\n".join(subgraph_lines) if subgraph_lines else "",
            "related_nodes": expanded_nodes,
        }

    def build_context_prompt(self, query: str, top_k: int = 5) -> str:
        """Monta o bloco de contexto pronto para injetar no prompt do LLM."""
        result = self.retrieve(query, top_k=top_k)

        lines = ["### CONTEXTO RECUPERADO (memória convexa)"]

        # Chunks
        if result["chunks"]:
            lines.append("\n**Trechos relevantes:**")
            for i, chunk in enumerate(result["chunks"], 1):
                lines.append(f"{i}. {chunk['text'][:300]}")

        # Entidades
        if result["entities"]:
            lines.append(f"\n**Entidades:** {', '.join(result['entities'])}")

        # Subgrafo
        if result["subgraph"]:
            lines.append(f"\n{result['subgraph']}")

        lines.append("\n### FIM DO CONTEXTO")
        return "\n".join(lines)
