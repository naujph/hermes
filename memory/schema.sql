-- Schema da Memória Convexada do Hermes
-- SQLite nativo: nós (entidades), arestas (relações), chunks (texto vetorizado)

-- ── Entidades ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,           -- ex: "Juan", "Acumen Score"
    type        TEXT NOT NULL,           -- ex: "Person", "Project", "Organization"
    properties  TEXT DEFAULT '{}',     -- JSON arbitrário
    source      TEXT DEFAULT 'hermes',   -- de onde veio
    confidence  REAL DEFAULT 1.0,        -- 0.0 a 1.0
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_label ON nodes(label);

-- ── Relações ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS edges (
    id          TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,           -- nó origem
    target_id   TEXT NOT NULL,           -- nó destino
    relation    TEXT NOT NULL,           -- ex: "owns_project", "married_to"
    properties  TEXT DEFAULT '{}',
    confidence  REAL DEFAULT 1.0,
    created_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);

-- ── Chunks de texto vetorizados ──────────────────────────────────
CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    embedding   TEXT,                    -- JSON array de floats (768 dims)
    node_ids    TEXT DEFAULT '[]',       -- entidades mencionadas no chunk
    source      TEXT DEFAULT 'hermes',   -- ex: "telegram", "document"
    source_ref  TEXT,                    -- ex: "memory_v2", "chat_2026-06-07"
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);

-- ── Fila de ingestão ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingestion_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT NOT NULL,           -- texto cru
    media_type  TEXT DEFAULT 'text',   -- text | voice | photo | video | doc
    meta        TEXT DEFAULT '{}',     -- JSON
    status      TEXT DEFAULT 'pending',-- pending | processing | done | error
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON ingestion_queue(status);

-- ── Log de inferências ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inference_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query       TEXT NOT NULL,
    context_ids TEXT DEFAULT '[]',       -- chunks usados
    node_ids    TEXT DEFAULT '[]',       -- nós usados
    response_summary TEXT,               -- resumo da resposta
    new_facts   TEXT DEFAULT '[]',       -- fatos novos inferidos
    created_at  TEXT DEFAULT (datetime('now'))
);
