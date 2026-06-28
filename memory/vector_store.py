"""VectorStore — Armazenamento de chunks com embeddings (ChromaDB + MiniLM local).

Usa sentence-transformers 'all-MiniLM-L6-v2' (384 dims) rodando localmente.
Não depende de Ollama/WSL para embeddings.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class VectorStore:
    """Interface com ChromaDB para busca semântica de chunks."""

    def __init__(self, collection_name: str = "hermes_memory",
                 persist_dir: str | None = None):
        if persist_dir is None:
            persist_dir = str(Path(__file__).resolve().parent / "chroma_db")
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self._lock = threading.RLock()
        self._client = None
        self._collection = None
        self._embedding_fn = None
        self._ensure_collection()

    # ── Internals ────────────────────────────────────────────────────

    def _ensure_collection(self) -> None:
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

        with self._lock:
            if self._client is None:
                self._client = chromadb.PersistentClient(path=self.persist_dir)
            if self._collection is None:
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
            if self._embedding_fn is None:
                self._embedding_fn = SentenceTransformerEmbeddingFunction(
                    model_name="all-MiniLM-L6-v2",
                    device="cpu",
                )

    def _embed(self, texts: List[str]) -> List[List[float]]:
        """Gera embeddings via sentence-transformers."""
        return self._embedding_fn(texts)

    # ── Public API ───────────────────────────────────────────────────

    def add_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        """Adiciona chunks com embedding automático.

        chunks = [
            {"id": "chunk_01", "text": "...", "node_ids": ["node_1"], "source": "telegram", ...}
        ]
        """
        if not chunks:
            return

        texts = [c["text"] for c in chunks]
        embeddings = self._embed(texts)

        ids = [c["id"] for c in chunks]
        documents = texts
        metadatas = [
            {
                "node_ids": json.dumps(c.get("node_ids", [])),
                "source": c.get("source", "hermes"),
                "source_ref": c.get("source_ref", ""),
            }
            for c in chunks
        ]

        with self._lock:
            self._collection.add(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )

    def query(self, query_text: str, n_results: int = 5,
              node_filter: List[str] | None = None) -> List[Dict[str, Any]]:
        """Busca semântica. Retorna chunks ordenados por relevância."""
        embedding = self._embed([query_text])[0]

        where_filter = None
        if node_filter:
            # ChromaDB não suporta IN diretamente em metadata complexa,
            # então filtramos pós-busca
            pass

        with self._lock:
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=n_results * 2 if node_filter else n_results,
                include=["documents", "metadatas", "distances"],
            )

        chunks = []
        if results and results["ids"]:
            for i, cid in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                node_ids = json.loads(meta.get("node_ids", "[]"))

                if node_filter and not any(n in node_ids for n in node_filter):
                    continue

                chunks.append({
                    "id": cid,
                    "text": results["documents"][0][i] if results["documents"] else "",
                    "distance": results["distances"][0][i] if results["distances"] else 0.0,
                    "node_ids": node_ids,
                    "source": meta.get("source", ""),
                    "source_ref": meta.get("source_ref", ""),
                })

        # Re-ranking simples: menor distância primeiro
        chunks.sort(key=lambda x: x["distance"])
        return chunks[:n_results]

    def delete_chunks(self, chunk_ids: List[str]) -> None:
        with self._lock:
            self._collection.delete(ids=chunk_ids)

    def count(self) -> int:
        with self._lock:
            return self._collection.count()

    def get_stats(self) -> dict:
        return {
            "collection": self.collection_name,
            "persist_dir": self.persist_dir,
            "chunks": self.count(),
        }
