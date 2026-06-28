"""InferenceEngine — Extrai relações semânticas explícitas via LLM.

Recebe: texto + entidades detectadas
Envia para LLM: "Quais relações existem entre essas entidades?"
Recebe de volta: JSON com relações tipadas
Salva: arestas nomeadas no grafo (não genéricas)
"""
from __future__ import annotations

import json
import re
import threading
import traceback
from typing import Any, Dict, List, Tuple

from app.llm_client import UnifiedLLMClient
from hermes.memory.graph_manager import GraphManager

# Ensure env is loaded
from app.config import load_env
load_env()


class InferenceEngine:
    """Extrai relações semânticas de texto usando LLM."""

    # Relações permitidas (controlled vocabulary)
    ALLOWED_RELATIONS = [
        "married_to", "works_at", "owns_project", "uses_tech", "studying_for",
        "has_pet", "lives_in", "collaborates_with", "depends_on", "part_of",
        "created_by", "manages", "friend_of", "family_of", "invests_in",
        "competes_with", "located_at", "has_skill", "interested_in",
    ]

    def __init__(self, model: str | None = None,
                 api_key: str | None = None,
                 base_url: str | None = None,
                 graph: GraphManager | None = None):
        self.llm = UnifiedLLMClient(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=60,
        )
        self.graph = graph or GraphManager()
        self._lock = threading.Lock()

    def infer_relations(self, text: str, entity_labels: List[str]) -> List[Dict[str, str]]:
        """Pede ao LLM para extrair relações entre entidades mencionadas.

        Retorna lista de dicts: [{"from": "Juan", "to": "Alianely", "relation": "married_to"}]
        """
        if len(entity_labels) < 2:
            return []

        prompt = self._build_prompt(text, entity_labels)

        try:
            raw = self._call_llm(prompt)
            return self._parse_response(raw, entity_labels)
        except Exception as exc:
            print(f"[INFERENCE] Erro: {exc}")
            traceback.print_exc()
            return []

    def _build_prompt(self, text: str, entities: List[str]) -> str:
        rels_str = ", ".join(self.ALLOWED_RELATIONS)
        entities_str = "\n".join(f"  - {e}" for e in entities)
        return (
            f"Analise o texto abaixo e identifique relações SEMÂNTICAS entre as entidades listadas.\n\n"
            f"Texto:\n{text[:800]}\n\n"
            f"Entidades:\n{entities_str}\n\n"
            f"Relações possíveis: {rels_str}\n\n"
            f"Responda APENAS com um JSON array válido no formato:\n"
            f'[{{"from": "entidade_A", "to": "entidade_B", "relation": "married_to"}}]\n'
            f"Se não houver relações claras, responda: []\n"
            f"JSON:"
        )

    def _call_llm(self, prompt: str) -> str:
        resp = self.llm.complete(
            prompt,
            temperature=0.2,  # Baixa criatividade, mais factual
            max_tokens=500,
        )
        if resp.error:
            raise RuntimeError(resp.error)
        return resp.content

    def _parse_response(self, raw: str, valid_entities: List[str]) -> List[Dict[str, str]]:
        """Extrai JSON da resposta do LLM e valida."""
        # Tenta extrair JSON entre colchetes
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []

        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

        results = []
        valid_lower = {e.lower() for e in valid_entities}

        for item in parsed:
            if not isinstance(item, dict):
                continue
            frm = item.get("from", item.get("source", "")).strip()
            to = item.get("to", item.get("target", "")).strip()
            rel = item.get("relation", item.get("rel", "")).strip().lower()

            # Valida entidades
            if frm.lower() not in valid_lower or to.lower() not in valid_lower:
                continue

            # Normaliza relação
            rel = self._normalize_relation(rel)
            if rel not in self.ALLOWED_RELATIONS:
                continue

            results.append({"from": frm, "to": to, "relation": rel})

        return results

    def _normalize_relation(self, rel: str) -> str:
        """Mapeia variações para relação canônica."""
        mapping = {
            "married": "married_to", "spouse": "married_to", "wife": "married_to", "husband": "married_to",
            "works": "works_at", "employed": "works_at", "job": "works_at",
            "owns": "owns_project", "created": "owns_project", "built": "owns_project",
            "uses": "uses_tech", "tech": "uses_tech", "stack": "uses_tech",
            "studying": "studying_for", "learning": "studying_for", "course": "studying_for",
            "pet": "has_pet", "dog": "has_pet", "cat": "has_pet",
            "lives": "lives_in", "live": "lives_in", "city": "lives_in",
            "collaborates": "collaborates_with", "helped": "collaborates_with", "helps": "collaborates_with",
            "depends": "depends_on", "needs": "depends_on", "requires": "depends_on",
            "part": "part_of", "component": "part_of",
            "friend": "friend_of", "buddy": "friend_of",
            "family": "family_of", "parent": "family_of", "child": "family_of",
            "invests": "invests_in", "invested": "invests_in",
            "competes": "competes_with", "rival": "competes_with",
            "located": "located_at", "address": "located_at",
            "skill": "has_skill", "expert": "has_skill",
            "interested": "interested_in", "likes": "interested_in",
        }
        return mapping.get(rel, rel)

    def process_chunk(self, text: str, chunk_node_ids: List[str]) -> int:
        """Pipeline completo: extrai entidades do chunk, pergunta ao LLM, salva arestas.

        Retorna número de arestas criadas.
        """
        if len(chunk_node_ids) < 2:
            return 0

        # Pega labels dos nós
        labels = []
        for nid in chunk_node_ids:
            nodes = self.graph.find_node(node_id=nid)
            if nodes:
                labels.append(nodes[0][1].get("label", nid))

        if len(labels) < 2:
            return 0

        relations = self.infer_relations(text, labels)
        created = 0

        for rel in relations:
            # Resolve label → node_id
            from_nodes = self.graph.find_node(label=rel["from"])
            to_nodes = self.graph.find_node(label=rel["to"])

            if not from_nodes or not to_nodes:
                continue

            from_id = from_nodes[0][0]
            to_id = to_nodes[0][0]

            # Evita duplicados exatos
            existing = self._edge_exists(from_id, to_id, rel["relation"])
            if existing:
                continue

            self.graph.add_edge(
                from_id, to_id,
                relation=rel["relation"],
                properties={"inferred_by": "llm", "source_text": text[:200]},
                confidence=0.85,
            )
            created += 1

        return created

    def _edge_exists(self, source_id: str, target_id: str, relation: str) -> bool:
        """Verifica se aresta idêntica já existe."""
        with self._lock:
            if self.graph._graph.has_edge(source_id, target_id):
                data = self.graph._graph[source_id][target_id]
                return data.get("relation") == relation
        return False
