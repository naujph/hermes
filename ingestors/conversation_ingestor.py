"""ConversationIngestor — Ingere conversas de qualquer fonte e extrai inteligência de assessoria.

Recebe: mensagens estruturadas (autor, texto, timestamp, fonte)
Faz:    NER + LLM → entidades, intenções, tarefas, sentimento → grafo + alertas
"""
from __future__ import annotations

import json
import re
import traceback
from datetime import datetime, timezone
from typing import Any

from app.llm_client import UnifiedLLMClient
from hermes.memory.graph_manager import GraphManager
from hermes.memory.vector_store import VectorStore
from hermes.memory.ingestor import Ingestor


class ConversationIngestor:
    """Ingere conversas e alimenta a memória convexa + alertas."""

    def __init__(self, graph: GraphManager | None = None, vector_store: VectorStore | None = None, llm: UnifiedLLMClient | None = None):
        self.graph = graph or GraphManager()
        self.vector = vector_store or VectorStore()
        self.base_ingestor = Ingestor(graph=self.graph, vector_store=self.vector)
        self.llm = llm or UnifiedLLMClient()

    # ── Interface pública ────────────────────────────────────────────

    def ingest(self, messages: list[dict], source: str = "generic", contact_name: str = "") -> dict:
        """
        messages: [{"author": str, "text": str, "timestamp": str (ISO), "is_me": bool}, ...]
        source: "whatsapp" | "email" | "other"
        contact_name: nome do contato (ex: "Cliente João Silva")
        """
        if not messages:
            return {"success": True, "ingested": 0, "alert": None}

        # 1. Monta thread única
        thread_text = self._build_thread(messages)

        # 2. Extrai inteligência via LLM
        extraction = self._extract_intelligence(thread_text, contact_name, source)

        # 3. Alimenta grafo + vetor
        self._feed_memory(thread_text, extraction, contact_name)

        # 4. Cria alerta se necessário
        alert = self._maybe_create_alert(extraction, contact_name, source)

        return {
            "success": True,
            "ingested": len(messages),
            "extraction": extraction,
            "alert": alert,
        }

    # ── Internals ────────────────────────────────────────────────────

    def _build_thread(self, messages: list[dict]) -> str:
        lines = []
        for m in messages:
            ts = m.get("timestamp", "")
            author = m.get("author", "Desconhecido")
            text = m.get("text", "").strip()
            if not text:
                continue
            lines.append(f"[{ts}] {author}: {text}")
        return "\n".join(lines)

    def _extract_intelligence(self, thread_text: str, contact_name: str, source: str) -> dict:
        """Usa LLM para extrair estrutura da conversa."""
        prompt = self._build_extraction_prompt(thread_text, contact_name, source)
        return self._call_llm(prompt)

    def _build_extraction_prompt(self, thread_text: str, contact_name: str, source: str) -> str:
        return (
            "Você é um assistente de assessoria financeira. Analise a conversa abaixo e extraia um JSON estrito.\n\n"
            "Regras:\n"
            "- sentimento: positivo | neutro | negativo | urgente\n"
            "- intencao_cliente: interesse | duvida | reclamacao | follow_up | outro\n"
            "- tarefas_para_assessor: lista de ações que Juan (assessor) deve fazer\n"
            "- entidades: pessoas, empresas, valores monetários, datas, produtos financeiros mencionados\n"
            "- topicos: assuntos principais da conversa (máx 5)\n"
            "- proximo_passo_sugerido: uma frase sobre o que fazer em seguida\n\n"
            "Responda APENAS com JSON válido, sem markdown.\n\n"
            f"Fonte: {source}\n"
            f"Contato: {contact_name}\n\n"
            f"--- CONVERSA ---\n{thread_text[:4000]}\n--- FIM ---"
        )

    def _call_llm(self, prompt: str) -> dict:
        """Chama o LLM via UnifiedLLMClient e retorna JSON parseado."""
        try:
            result = self.llm.extract_json(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2048,
            )
            return result.get("parsed") or {}
        except Exception:
            traceback.print_exc()
            return {}

    def _parse_llm_json(self, text: str) -> dict:
        """Tenta extrair JSON da resposta do LLM."""
        text = text.strip()
        # Remove markdown code block
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Tenta extrair o primeiro bloco JSON
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
        return {
            "sentimento": "neutro",
            "intencao_cliente": "outro",
            "tarefas_para_assessor": [],
            "entidades": [],
            "topicos": [],
            "proximo_passo_sugerido": "",
        }

    def _feed_memory(self, thread_text: str, extraction: dict, contact_name: str) -> None:
        """Alimenta grafo e vetor com a conversa."""
        # Chunks da conversa
        chunks = self.base_ingestor._chunk_text(thread_text, max_len=500, overlap=50)
        for chunk in chunks:
            try:
                self.base_ingestor._ingest_chunk(chunk, source="conversation")
            except Exception:
                pass  # não quebra se vetor der erro

        # Nós principais da extração
        entidades = extraction.get("entidades", [])
        if contact_name and contact_name not in [e.get("nome", "") for e in entidades]:
            entidades.append({"nome": contact_name, "tipo": "pessoa", "contexto": "contato de assessoria"})

        for ent in entidades:
            if isinstance(ent, str):
                ent = {"nome": ent, "tipo": "desconhecido", "contexto": ""}
            nome = ent.get("nome", "")
            if not nome:
                continue
            try:
                self.graph.add_node(
                    name=nome,
                    node_type=ent.get("tipo", "entity"),
                    context=ent.get("contexto", ""),
                    source="conversation",
                )
                if contact_name and contact_name != nome:
                    self.graph.add_edge(contact_name, nome, relation="mencionado_em", weight=0.7)
            except Exception:
                pass

        # Tarefas como nós
        for tarefa in extraction.get("tarefas_para_assessor", []):
            if isinstance(tarefa, str) and tarefa.strip():
                try:
                    tid = re.sub(r"\W+", "_", tarefa.strip().lower())[:40]
                    self.graph.add_node(
                        name=f"tarefa:{tid}",
                        node_type="tarefa",
                        context=tarefa.strip(),
                        source="conversation",
                    )
                    if contact_name:
                        self.graph.add_edge(contact_name, f"tarefa:{tid}", relation="gera_tarefa", weight=0.9)
                except Exception:
                    pass

    def _maybe_create_alert(self, extraction: dict, contact_name: str, source: str) -> dict | None:
        """Cria alerta se detectar urgência ou tarefas."""
        sentimento = extraction.get("sentimento", "neutro")
        tarefas = extraction.get("tarefas_para_assessor", [])
        proximo = extraction.get("proximo_passo_sugerido", "")

        if sentimento == "urgente":
            return {
                "alert_type": "urgencia",
                "title": f"Urgência com {contact_name}",
                "description": f"Conversa {source} detectada como urgente. Próximo passo: {proximo}",
            }

        if tarefas:
            tarefas_txt = "; ".join(tarefas[:3])
            return {
                "alert_type": "sugestao",
                "title": f"Tarefas pendentes — {contact_name}",
                "description": f"Tarefas sugeridas: {tarefas_txt}. Próximo passo: {proximo}",
            }

        if sentimento == "positivo" and extraction.get("intencao_cliente") == "interesse":
            return {
                "alert_type": "insight",
                "title": f"Interesse detectado — {contact_name}",
                "description": f"Cliente demonstrou interesse em conversa {source}. Próximo passo: {proximo}",
            }

        return None
