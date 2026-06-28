"""BackgroundRunner — Orquestrador de tarefas de manutenção da memória.

Roda em thread separada, executa periodicamente:
  - inference: extrai relações de chunks pendentes
  - dedup: funde nós duplicados
  - cleanup: remove órfãos
  - enrichment: revisa nós isolados
"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

from hermes.memory.cleanup import Cleanup
from hermes.memory.dedup import Deduplicator
from hermes.memory.enrichment import Enrichment
from hermes.memory.inference_engine import InferenceEngine
from hermes.memory.graph_manager import GraphManager
from hermes.memory.vector_store import VectorStore


class BackgroundRunner:
    """Agenda e executa tarefas de manutenção."""

    def __init__(self,
                 graph: GraphManager | None = None,
                 vector_store: VectorStore | None = None):
        self.graph = graph or GraphManager()
        self.vector = vector_store or VectorStore()
        self.inference = InferenceEngine(graph=self.graph)
        self.dedup = Deduplicator(graph=self.graph)
        self.cleanup = Cleanup(graph=self.graph, vector_store=self.vector)
        self.enrichment = Enrichment(graph=self.graph, inference=self.inference)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pending_chunks: list = []  # fila de chunks para inferir relações
        self._lock = threading.Lock()

    # ── Scheduling ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Inicia thread de background."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[BG] BackgroundRunner iniciado")

    def stop(self) -> None:
        """Sinaliza parada."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        """Loop principal. Roda a cada 60s por padrão."""
        inference_counter = 0
        dedup_counter = 0
        cleanup_counter = 0
        enrichment_counter = 0

        while not self._stop.is_set():
            try:
                # A cada 60s: inference em chunks pendentes
                inference_counter += 1
                if inference_counter >= 1:
                    self._run_inference()
                    inference_counter = 0

                # A cada 5 min: dedup
                dedup_counter += 1
                if dedup_counter >= 5:
                    self._run_dedup()
                    dedup_counter = 0

                # A cada 10 min: cleanup
                cleanup_counter += 1
                if cleanup_counter >= 10:
                    self._run_cleanup()
                    cleanup_counter = 0

                # A cada 30 min: enrichment
                enrichment_counter += 1
                if enrichment_counter >= 30:
                    self._run_enrichment()
                    enrichment_counter = 0

            except Exception as exc:
                print(f"[BG] Erro no loop: {exc}")
                traceback.print_exc()

            # Espera 60s ou até stop
            self._stop.wait(60)

    # ── Task runners ─────────────────────────────────────────────────

    def _run_inference(self) -> None:
        """Processa chunks pendentes e extrai relações semânticas."""
        with self._lock:
            chunks = self._pending_chunks[:10]  # batch de 10
            self._pending_chunks = self._pending_chunks[10:]

        if not chunks:
            return

        total_edges = 0
        for chunk_info in chunks:
            try:
                edges = self.inference.process_chunk(
                    chunk_info.get("text", ""),
                    chunk_info.get("node_ids", []),
                )
                total_edges += edges
            except Exception:
                pass

        if total_edges > 0:
            print(f"[BG] Inference: {total_edges} arestas criadas de {len(chunks)} chunks")

    def _run_dedup(self) -> None:
        """Executa deduplicação fuzzy."""
        try:
            stats = self.dedup.run()
            if stats["merged"] > 0:
                print(f"[BG] Dedup: {stats['merged']} nós fundidos")
        except Exception as exc:
            print(f"[BG] Dedup erro: {exc}")

    def _run_cleanup(self) -> None:
        """Executa limpeza de órfãos."""
        try:
            stats = self.cleanup.run()
            if stats["nodes_removed"] > 0 or stats["chunks_removed"] > 0:
                print(f"[BG] Cleanup: {stats}")
        except Exception as exc:
            print(f"[BG] Cleanup erro: {exc}")

    def _run_enrichment(self) -> None:
        """Executa enriquecimento de nós isolados."""
        try:
            stats = self.enrichment.run()
            if stats["new_edges_created"] > 0:
                print(f"[BG] Enrichment: {stats}")
        except Exception as exc:
            print(f"[BG] Enrichment erro: {exc}")

    # ── Public API ───────────────────────────────────────────────────

    def queue_chunk(self, text: str, node_ids: List[str]) -> None:
        """Adiciona chunk na fila para inferência de relações."""
        with self._lock:
            self._pending_chunks.append({"text": text, "node_ids": node_ids})

    def force_run(self, task: str) -> dict:
        """Força execução imediata de uma tarefa."""
        if task == "inference":
            self._run_inference()
            return {"task": "inference", "queue_size": len(self._pending_chunks)}
        if task == "dedup":
            return self.dedup.run()
        if task == "cleanup":
            return self.cleanup.run()
        if task == "enrichment":
            return self.enrichment.run()
        return {"error": f"Tarefa desconhecida: {task}"}

    def status(self) -> dict:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "pending_chunks": len(self._pending_chunks),
            "graph": self.graph.get_stats(),
        }
