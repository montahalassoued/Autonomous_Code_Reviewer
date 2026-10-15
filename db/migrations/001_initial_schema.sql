-- Code index of main + review history. Schema described in docs/ARCHITECTURE.md.

CREATE TABLE repos (
    id              BIGSERIAL PRIMARY KEY,
    full_name       TEXT NOT NULL UNIQUE,
    default_branch  TEXT NOT NULL DEFAULT 'main',
    indexed_sha     TEXT,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    embedding_model TEXT
);

CREATE TABLE files (
    id           BIGSERIAL PRIMARY KEY,
    repo_id      BIGINT NOT NULL REFERENCES repos (id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    language     TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    -- Plain text of non-Python files, kept for grep; NULL for Python (symbols hold the code).
    content      TEXT,
    UNIQUE (repo_id, path)
);

CREATE TABLE symbols (
    id             BIGSERIAL PRIMARY KEY,
    file_id        BIGINT NOT NULL REFERENCES files (id) ON DELETE CASCADE,
    qualified_name TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('function', 'method', 'class_header', 'module')),
    parent_id      BIGINT REFERENCES symbols (id) ON DELETE CASCADE,
    signature      TEXT,
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    code           TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    norm_hash      TEXT NOT NULL,
    UNIQUE (file_id, qualified_name)
);

-- Call resolution looks symbols up by name across the repo.
CREATE INDEX symbols_qualified_name_idx ON symbols (qualified_name);

CREATE TABLE edges (
    src_id BIGINT NOT NULL REFERENCES symbols (id) ON DELETE CASCADE,
    dst_id BIGINT NOT NULL REFERENCES symbols (id) ON DELETE CASCADE,
    kind   TEXT NOT NULL CHECK (kind IN ('calls', 'imports', 'inherits', 'tested_by')),
    PRIMARY KEY (src_id, dst_id, kind)
);

-- Callers and tests of a symbol are found from the destination side.
CREATE INDEX edges_dst_idx ON edges (dst_id, kind);

CREATE TABLE unresolved_calls (
    src_id BIGINT NOT NULL REFERENCES symbols (id) ON DELETE CASCADE,
    name   TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (src_id, name)
);

CREATE TABLE reviews (
    id                 BIGSERIAL PRIMARY KEY,
    repo_id            BIGINT NOT NULL REFERENCES repos (id) ON DELETE CASCADE,
    -- "pr:<number>" or "push:<branch>".
    target             TEXT NOT NULL,
    base_sha           TEXT,
    head_sha           TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'running', 'posted', 'skipped', 'failed')),
    skipped_reason     TEXT,
    llm_calls          INTEGER NOT NULL DEFAULT 0,
    tokens_in          INTEGER NOT NULL DEFAULT 0,
    tokens_out         INTEGER NOT NULL DEFAULT 0,
    latency_ms         INTEGER,
    context_symbol_ids BIGINT[] NOT NULL DEFAULT '{}',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The dedupe key: one review per commit per target.
    UNIQUE (repo_id, target, head_sha)
);

CREATE TABLE findings (
    id                BIGSERIAL PRIMARY KEY,
    review_id         BIGINT NOT NULL REFERENCES reviews (id) ON DELETE CASCADE,
    category          TEXT NOT NULL,
    severity          TEXT NOT NULL CHECK (severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')),
    file_path         TEXT NOT NULL,
    line              INTEGER NOT NULL,
    symbol_name       TEXT,
    message           TEXT NOT NULL,
    recommendation    TEXT NOT NULL,
    -- Survives line shifts, so a finding already posted is not posted again.
    fingerprint       TEXT NOT NULL,
    github_comment_id BIGINT
);

CREATE INDEX findings_fingerprint_idx ON findings (fingerprint);
