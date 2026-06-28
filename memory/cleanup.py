"""Cleanup — Remove nós órfãos e chunks velhos.

Regras:
  - Nó sem arestas e com confiança < 0.3 → deleta
  - Nó sem arestas e criado há +30 dias → deleta
  - Chunk sem node_links e criado há +90 dias → deleta
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Dict, List

from hermes.memory.graph_manager import GraphManager
from hermes.memory.vector_store import VectorStore


class Cleanup:
    """Garbage collector da memória convexa."""

    def __init__(self, graph: GraphManager | None = None,
                 vector_store: VectorStore | None = None):
        self.graph = graph or GraphManager()
        self.vector = vector_store or VectorStore()

    def run(self) -> Dict[str, int]:
        """Executa limpeza. Retorna estatísticas."""
        removed_nodes = self._cleanup_nodes()
        removed_chunks = self._cleanup_chunks()

        return {
            "nodes_removed": removed_nodes,
            "chunks_removed": removed_chunks,
        }

    def _cleanup_nodes(self) -> int:
        """Remove nós órfãos (sem arestas) e baixa confiança."""
        g = self.graph._graph
        now = datetime.now(timezone.utc)
        to_remove: List[str] = []

        for nid, data in list(g.nodes(data=True)):
            degree = g.degree(nid)
            if degree > 0:
                continue  # tem arestas, mantém

            confidence = data.get("confidence", 1.0)
            created = data.get("created_at", "")

            # Regra 1: sem arestas + confiança baixa
            if confidence < 0.3:
                to_remove.append(nid)
                continue

            # Regra 2: sem arestas + criado há mais de 30 dias
            if created:
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    age_days = (now - created_dt).days
                    if age_days > 30:
                        to_remove.append(nid)
                except Exception:
                    pass

        for nid in to_remove:
            g.remove_node(nid)

        # Sync DB
        if to_remove:
            conn = sqlite3.connect(self.graph.db_path)
            placeholders = ",".join("?" * len(to_remove))
            conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", to_remove)
            conn.commit()
            conn.close()

        return len(to_remove)

    def _cleanup_chunks(self) -> int:
        """Remove chunks órfãos do vector store.

        Infelizmente ChromaDB não tem query por idade fácil sem metadata,
        então essa versão limpa chunks que não referenciam nós existentes.
        """
        # Placeholder: ChromaDB não expõe data de criação facilmente
        # Versão futura: adicionar 'created_at' nos metadatas do chunk
        return 0
