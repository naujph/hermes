"""Hermes Memory — Sistema de memória convexa local.

Módulos:
  graph_manager      → Grafo de entidades (NetworkX + SQLite)
  vector_store       → Chunks vetorizados (ChromaDB + MiniLM)
  ingestor           → Pipeline de ingestão (chunk + NER + store)
  retriever          → Recuperação semântica + expansão de grafo
  inference_engine   → Extrai relações semânticas via LLM
  dedup              → Deduplicação fuzzy de nós
  cleanup            → Remove órfãos e lixo
  enrichment         → Revisa nós isolados e propõe links
  background_runner  → Orquestra tudo em background
  migrate_v2         → Migra memory_store.json para o novo sistema
"""
from hermes.memory.graph_manager import GraphManager
from hermes.memory.vector_store import VectorStore
from hermes.memory.ingestor import Ingestor
from hermes.memory.retriever import Retriever
from hermes.memory.inference_engine import InferenceEngine
from hermes.memory.dedup import Deduplicator
from hermes.memory.cleanup import Cleanup
from hermes.memory.enrichment import Enrichment
from hermes.memory.background_runner import BackgroundRunner

__all__ = [
    "GraphManager", "VectorStore", "Ingestor", "Retriever",
    "InferenceEngine", "Deduplicator", "Cleanup", "Enrichment", "BackgroundRunner",
]
