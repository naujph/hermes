"""Dedup — Deduplicação fuzzy de nós por embedding de label.

Se dois nós têm labels similares (ex: "A1" e "A1 Investimentos")
e mesmo type, funde em um só.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer

from hermes.memory.graph_manager import GraphManager


class Deduplicator:
    """Encontra e funde nós duplicados fuzzy."""

    def __init__(self, graph: GraphManager | None = None,
                 threshold: float = 0.88):
        self.graph = graph or GraphManager()
        self.threshold = threshold
        self._model = SentenceTransformer("all-MiniLM-L6-v2")
        self._lock = threading.Lock()

    def find_duplicates(self) -> List[Tuple[str, str, float]]:
        """Retorna pares de nós similares: [(keep_id, merge_id, score), ...]."""
        nodes_by_type: Dict[str, List[Tuple[str, str]]] = {}

        for nid, data in self.graph._graph.nodes(data=True):
            t = data.get("type", "unknown")
            label = data.get("label", "")
            if not label:
                continue
            nodes_by_type.setdefault(t, []).append((nid, label))

        duplicates = []

        for type_, items in nodes_by_type.items():
            if len(items) < 2:
                continue

            labels = [label for _, label in items]
            embeddings = self._model.encode(labels, convert_to_numpy=True)

            # Matriz de similaridade cosseno
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            normed = embeddings / (norms + 1e-8)
            sim_matrix = normed @ normed.T

            n = len(items)
            for i in range(n):
                for j in range(i + 1, n):
                    score = float(sim_matrix[i, j])
                    if score >= self.threshold:
                        # Decide qual manter: maior confiança / mais arestas
                        id_i, label_i = items[i]
                        id_j, label_j = items[j]
                        deg_i = self.graph._graph.degree(id_i)
                        deg_j = self.graph._graph.degree(id_j)
                        if deg_i >= deg_j:
                            duplicates.append((id_i, id_j, score))
                        else:
                            duplicates.append((id_j, id_i, score))

        return duplicates

    def merge(self, keep_id: str, merge_id: str) -> None:
        """Funde merge_id em keep_id: move arestas, atualiza chunks."""
        with self._lock:
            g = self.graph._graph

            if keep_id not in g or merge_id not in g:
                return

            # Move todas as arestas de merge_id para keep_id
            # Outgoing
            for _, target, data in g.out_edges(merge_id, data=True):
                if not g.has_edge(keep_id, target):
                    self.graph.add_edge(
                        keep_id, target,
                        relation=data.get("relation", "relacionado"),
                        properties=data.get("properties", {}),
                        confidence=data.get("confidence", 1.0),
                    )

            # Incoming
            for source, _, data in g.in_edges(merge_id, data=True):
                if not g.has_edge(source, keep_id):
                    self.graph.add_edge(
                        source, keep_id,
                        relation=data.get("relation", "relacionado"),
                        properties=data.get("properties", {}),
                        confidence=data.get("confidence", 1.0),
                    )

            # Atualiza propriedades: mergeia
            keep_data = dict(g.nodes[keep_id])
            merge_data = dict(g.nodes[merge_id])
            merged_props = {**merge_data.get("properties", {}),
                            **keep_data.get("properties", {})}
            keep_data["properties"] = merged_props
            # Atualiza no grafo
            for k, v in keep_data.items():
                g.nodes[keep_id][k] = v

            # Salva no DB
            self.graph._save_node(keep_id)

            # Deleta merge_id do grafo e DB
            g.remove_node(merge_id)
            import sqlite3
            conn = sqlite3.connect(self.graph.db_path)
            conn.execute("DELETE FROM nodes WHERE id = ?", (merge_id,))
            conn.execute("DELETE FROM edges WHERE source_id = ? OR target_id = ?",
                         (merge_id, merge_id))
            conn.commit()
            conn.close()

    def run(self) -> Dict[str, int]:
        """Roda deduplicação completa. Retorna estatísticas."""
        dups = self.find_duplicates()
        merged = 0
        for keep, merge, score in dups:
            self.merge(keep, merge)
            merged += 1

        return {"duplicates_found": len(dups), "merged": merged}
