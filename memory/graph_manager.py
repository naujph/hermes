"""GraphManager — Grafo de conhecimento persistente (NetworkX + SQLite).

Nós = entidades (pessoas, projetos, conceitos...)
Arestas = relações semânticas entre entidades
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx


class GraphManager:
    """Gerencia o grafo de conhecimento do Hermes."""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            base = Path(__file__).resolve().parent
            db_path = str(base / "hermes_memory.db")
        self.db_path = db_path
        self._lock = threading.RLock()
        self._graph: nx.DiGraph = nx.DiGraph()
        self._ensure_schema()
        self._load()

    # ── Internals ────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        """Cria as tabelas no SQLite se não existirem."""
        schema = Path(__file__).resolve().parent / "schema.sql"
        if not schema.exists():
            return
        conn = self._conn()
        try:
            conn.executescript(schema.read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()

    def _load(self) -> None:
        """Carrega nós e arestas do SQLite para o NetworkX em RAM."""
        with self._lock:
            self._graph.clear()
            conn = self._conn()
            try:
                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nodes'")
                if cur.fetchone() is None:
                    return  # banco vazio, nada a carregar

                for row in conn.execute("SELECT * FROM nodes"):
                    self._graph.add_node(
                        row["id"],
                        label=row["label"],
                        type=row["type"],
                        properties=json.loads(row["properties"] or "{}"),
                        confidence=row["confidence"],
                        source=row["source"],
                        created_at=row["created_at"],
                    )
                for row in conn.execute("SELECT * FROM edges"):
                    self._graph.add_edge(
                        row["source_id"],
                        row["target_id"],
                        relation=row["relation"],
                        properties=json.loads(row["properties"] or "{}"),
                        confidence=row["confidence"],
                        id=row["id"],
                    )
            finally:
                conn.close()

    def _save_node(self, node_id: str) -> None:
        data = self._graph.nodes[node_id]
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO nodes
                   (id, label, type, properties, source, confidence, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    node_id,
                    data.get("label", ""),
                    data.get("type", "unknown"),
                    json.dumps(data.get("properties", {}), ensure_ascii=False),
                    data.get("source", "hermes"),
                    data.get("confidence", 1.0),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _save_edge(self, edge_id: str, u: str, v: str) -> None:
        data = self._graph[u][v]
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO edges
                   (id, source_id, target_id, relation, properties, confidence)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    edge_id,
                    u,
                    v,
                    data.get("relation", "relacionado"),
                    json.dumps(data.get("properties", {}), ensure_ascii=False),
                    data.get("confidence", 1.0),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # ── Public API ─────────────────────────────────────────────────

    def add_node(self, label: str, type_: str, properties: dict | None = None,
                 node_id: str | None = None, source: str = "hermes",
                 confidence: float = 1.0) -> str:
        """Adiciona ou atualiza um nó. Retorna o ID."""
        properties = properties or {}
        # Normaliza ID
        if node_id is None:
            # Tenta reutilizar nó existente pelo label+type
            existing = self.find_node(label=label, type_=type_)
            if existing:
                node_id = existing[0][0]
            else:
                node_id = f"{type_.lower()}_{uuid.uuid4().hex[:8]}"

        with self._lock:
            self._graph.add_node(
                node_id,
                label=label,
                type=type_,
                properties=properties,
                source=source,
                confidence=confidence,
            )
            self._save_node(node_id)
        return node_id

    def add_edge(self, source_id: str, target_id: str, relation: str,
                 properties: dict | None = None,
                 edge_id: str | None = None, confidence: float = 1.0) -> str:
        """Adiciona uma relação entre dois nós."""
        properties = properties or {}
        if edge_id is None:
            edge_id = f"rel_{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._graph.add_edge(
                source_id, target_id,
                relation=relation,
                properties=properties,
                confidence=confidence,
                id=edge_id,
            )
            self._save_edge(edge_id, source_id, target_id)
        return edge_id

    def relate(self, source_label: str, target_label: str, relation: str,
               source_type: str = "Person", target_type: str = "Person",
               properties: dict | None = None) -> Tuple[str, str]:
        """Conveniência: cria nós (se não existirem) e liga. Retorna (source_id, target_id)."""
        sid = self.add_node(source_label, source_type)
        tid = self.add_node(target_label, target_type)
        self.add_edge(sid, tid, relation, properties)
        return sid, tid

    def find_node(self, label: str | None = None, type_: str | None = None,
                  node_id: str | None = None) -> List[Tuple[str, dict]]:
        """Busca nós. Retorna [(id, attrs), ...]."""
        results = []
        with self._lock:
            for nid, data in self._graph.nodes(data=True):
                if node_id and nid != node_id:
                    continue
                if label and data.get("label", "").lower() != label.lower():
                    continue
                if type_ and data.get("type", "").lower() != type_.lower():
                    continue
                results.append((nid, dict(data)))
        return results

    def get_neighbors(self, node_id: str, hops: int = 1,
                      relation_filter: str | None = None) -> List[Tuple[str, str, dict]]:
        """Retorna vizinhos até N hops. [(neighbor_id, relation, edge_data), ...]."""
        with self._lock:
            if node_id not in self._graph:
                return []

            nodes_at_distance: dict[int, set] = {0: {node_id}}
            visited = {node_id}
            edges_found = []

            for d in range(1, hops + 1):
                nodes_at_distance[d] = set()
                for current in nodes_at_distance[d - 1]:
                    for _, neighbor, edata in self._graph.out_edges(current, data=True):
                        if relation_filter and edata.get("relation") != relation_filter:
                            continue
                        if neighbor not in visited:
                            visited.add(neighbor)
                            nodes_at_distance[d].add(neighbor)
                            edges_found.append((neighbor, edata.get("relation", ""), dict(edata)))
                    for neighbor, _, edata in self._graph.in_edges(current, data=True):
                        if relation_filter and edata.get("relation") != relation_filter:
                            continue
                        if neighbor not in visited:
                            visited.add(neighbor)
                            nodes_at_distance[d].add(neighbor)
                            edges_found.append((neighbor, edata.get("relation", ""), dict(edata)))
            return edges_found

    def get_subgraph_text(self, node_id: str, hops: int = 2) -> str:
        """Gera texto descritivo do subgrafo para injetar no prompt do LLM."""
        with self._lock:
            if node_id not in self._graph:
                return ""

            lines = [f"### Subgrafo de conhecimento (hops={hops})"]
            center = self._graph.nodes[node_id]
            lines.append(f"Centro: {center.get('label', node_id)} ({center.get('type', '?')})")

            for nid, rel, edata in self.get_neighbors(node_id, hops=hops):
                data = self._graph.nodes[nid]
                lines.append(f"  → [{rel}] {data.get('label', nid)} ({data.get('type', '?')})")

            return "\n".join(lines)

    def search_by_label(self, query: str) -> List[Tuple[str, dict]]:
        """Busca por substring no label (case-insensitive)."""
        q = query.lower()
        results = []
        with self._lock:
            for nid, data in self._graph.nodes(data=True):
                if q in data.get("label", "").lower():
                    results.append((nid, dict(data)))
        return results

    def list_by_type(self, type_: str) -> List[Tuple[str, dict]]:
        with self._lock:
            return [
                (nid, dict(data))
                for nid, data in self._graph.nodes(data=True)
                if data.get("type", "").lower() == type_.lower()
            ]

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "nodes": self._graph.number_of_nodes(),
                "edges": self._graph.number_of_edges(),
                "types": list(set(d.get("type", "unknown") for _, d in self._graph.nodes(data=True))),
            }
