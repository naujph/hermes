"""Ingestor — Pipeline de ingestão de conhecimento.

Fluxo: texto cru → chunks → NER (entidades) → grafo + vector store
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

import spacy

from hermes.memory.graph_manager import GraphManager
from hermes.memory.vector_store import VectorStore
from hermes.memory.background_runner import BackgroundRunner

# Carrega modelo spaCy PT uma vez
_nlp = None

def _get_nlp():
    global _nlp
    if _nlp is None:
        try:
            _nlp = spacy.load("pt_core_news_sm")
        except Exception:
            # Fallback: carrega em branco se modelo não existir
            _nlp = spacy.blank("pt")
    return _nlp


class Ingestor:
    """Consome texto de qualquer fonte e popula grafo + vetor."""

    def __init__(self, graph: GraphManager | None = None,
                 vector_store: VectorStore | None = None,
                 background: BackgroundRunner | None = None):
        self.graph = graph or GraphManager()
        self.vector = vector_store or VectorStore()
        self.background = background  # pode ser None se não quiser queue
        self._lock = threading.Lock()

    # ── Chunking ─────────────────────────────────────────────────────

    def _chunk_text(self, text: str, max_len: int = 400, overlap: int = 50) -> List[str]:
        """Divide texto em chunks com overlap."""
        if len(text) <= max_len:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + max_len
            if end >= len(text):
                chunks.append(text[start:])
                break
            # Tenta quebrar no espaço mais próximo
            split = text.rfind(" ", start, end)
            if split == -1 or split <= start:
                split = end
            chunks.append(text[start:split])
            start = split - overlap
            if start <= 0:
                start = split
        return chunks

    # ── NER ──────────────────────────────────────────────────────────

    def _extract_entities(self, text: str) -> List[Dict[str, Any]]:
        """Extrai entidades com spaCy + regras custom."""
        nlp = _get_nlp()
        doc = nlp(text)

        entities = []
        seen = set()

        # spaCy NER
        for ent in doc.ents:
            key = (ent.text.lower(), ent.label_)
            if key in seen:
                continue
            seen.add(key)
            entities.append({
                "text": ent.text,
                "label": ent.label_,
                "start": ent.start_char,
                "end": ent.end_char,
            })

        # Regras custom para o domínio do Juan
        custom_patterns = [
            (r"\b(Acumen Score|Lead Prospecting Engine|Hermes)\b", "Project"),
            (r"\b(Next\.js|React|Node\.js|Python|SQLite|PostgreSQL|Ollama|Streamlit)\b", "Technology"),
            (r"\b(A1 Investimentos|XP Inc|XP Investimentos|ANBIMA|ANCORD)\b", "Organization"),
            (r"\b(Warzone|Valorant)\b", "Game"),
            (r"\b(Alianely)\b", "Person"),
            (r"\b(Chico|Charlotte)\b", "Pet"),
        ]

        for pattern, label in custom_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                key = (match.group(1).lower(), label)
                if key in seen:
                    continue
                seen.add(key)
                entities.append({
                    "text": match.group(1),
                    "label": label,
                    "start": match.start(),
                    "end": match.end(),
                })

        return entities

    def _map_entity_type(self, spacy_label: str) -> str:
        """Mapeia labels do spaCy para tipos do nosso grafo."""
        mapping = {
            "PER": "Person",
            "ORG": "Organization",
            "LOC": "Location",
            "MISC": "Concept",
            "Project": "Project",
            "Technology": "Technology",
            "Game": "Hobby",
            "Pet": "Pet",
        }
        return mapping.get(spacy_label, "Concept")

    # ── Ingestion ────────────────────────────────────────────────────

    def ingest(self, text: str, source: str = "hermes",
               source_ref: str = "", node_links: List[str] | None = None) -> Dict[str, Any]:
        """Ingere texto e popula grafo + vetor."""
        with self._lock:
            # 1. Chunk
            chunks_text = self._chunk_text(text)

            # 2. Extrai entidades do texto COMPLETO
            entities = self._extract_entities(text)
            entity_ids: Dict[str, str] = {}

            # 3. Cria/atualiza nós no grafo
            for ent in entities:
                node_type = self._map_entity_type(ent["label"])
                nid = self.graph.add_node(
                    label=ent["text"],
                    type_=node_type,
                    properties={"ner_label": ent["label"]},
                    source=source,
                )
                entity_ids[ent["text"].lower()] = nid

            # 4. Cria chunks vetorizados
            chunk_records = []
            for i, chunk in enumerate(chunks_text):
                chunk_id = f"chunk_{uuid.uuid4().hex[:8]}"
                # Detecta quais entidades aparecem neste chunk
                chunk_node_ids = []
                for ent_text, nid in entity_ids.items():
                    if ent_text in chunk.lower():
                        chunk_node_ids.append(nid)
                # Adiciona links explícitos
                if node_links:
                    chunk_node_ids.extend(node_links)
                # Deduplica
                chunk_node_ids = list(dict.fromkeys(chunk_node_ids))

                chunk_records.append({
                    "id": chunk_id,
                    "text": chunk,
                    "node_ids": chunk_node_ids,
                    "source": source,
                    "source_ref": source_ref or f"ingested_{datetime.now(timezone.utc).isoformat()}",
                })

            self.vector.add_chunks(chunk_records)

            # 5. Enfileira chunks para inferência de relações semânticas (background)
            if self.background:
                for rec in chunk_records:
                    if len(rec["node_ids"]) >= 2:
                        self.background.queue_chunk(rec["text"], rec["node_ids"])

            # 6. Relaciona entidades entre si (co-ocorrência no mesmo texto)
            if len(entity_ids) > 1:
                ids_list = list(entity_ids.values())
                for i in range(len(ids_list)):
                    for j in range(i + 1, len(ids_list)):
                        self.graph.add_edge(
                            ids_list[i], ids_list[j],
                            relation="co_ocorre",
                            properties={"context": text[:200]},
                        )

            return {
                "chunks": len(chunk_records),
                "entities": len(entities),
                "entity_names": [e["text"] for e in entities],
            }

    def ingest_conversation(self, user_msg: str, response: str,
                            topic: str = "chat") -> Dict[str, Any]:
        """Ingere uma conversa completa (usuário + resposta)."""
        text = f"Usuario: {user_msg}\nHermes: {response}"
        return self.ingest(
            text=text,
            source="telegram",
            source_ref=f"conversation_{topic}_{datetime.now(timezone.utc).isoformat()}",
        )

    def ingest_profile_fact(self, key: str, value: str,
                            category: str = "profile") -> Dict[str, Any]:
        """Ingere um fato do perfil (ex: idade, formação)."""
        text = f"{key}: {value}"
        return self.ingest(
            text=text,
            source="profile",
            source_ref=f"profile_{category}",
        )

    def stats(self) -> dict:
        return {
            "graph": self.graph.get_stats(),
            "vector": self.vector.get_stats(),
        }
