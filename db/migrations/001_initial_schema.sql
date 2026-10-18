-- Code index of main + review history. Schema described in docs/ARCHITECTURE.md, the why in docs/DECISIONS.md.

CREATE TABLE repos (
    id             BIGSERIAL PRIMARY KEY,
    full_name      TEXT NOT NULL UNIQUE,
    default_branch TEXT NOT NULL DEFAULT 'main',
    -- Commit and indexer version the index was built from; both NULL until the first index.
    indexed_sha    TEXT,
    schema_version INTEGER,
    CHECK ((indexed_sha IS NULL) = (schema_version IS NULL))
);

CREATE TABLE files (
    id           BIGSERIAL PRIMARY KEY,
    repo_id      BIGINT NOT NULL REFERENCES repos (id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    language     TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    -- Plain text of small non-Python text files, kept for grep; NULL for Python (symbols hold the code).
    content      TEXT,
    -- Why the file was not parsed (syntax error, not UTF-8); its symbols are then missing.
    parse_error  TEXT,
    UNIQUE (repo_id, path)
);

CREATE TABLE symbols (
    id             BIGSERIAL PRIMARY KEY,
    file_id        BIGINT NOT NULL REFERENCES files (id) ON DELETE CASCADE,
    -- Bare name a call site uses ("login"); qualified_name is "Class.login", "login#2" or "<module>".
    name           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('function', 'method', 'class_header', 'module')),
    -- Set from the tree walk, never parsed from qualified_name (duplicate classes make it ambiguous).
    parent_id      BIGINT REFERENCES symbols (id) ON DELETE CASCADE,
    signature      TEXT,
    start_line     INTEGER NOT NULL CHECK (start_line >= 1),
    end_line       INTEGER NOT NULL,
    code           TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    CHECK (end_line >= start_line),
    UNIQUE (file_id, qualified_name)
);

-- Call resolution looks up the bare name in the same file, then across the repo.
CREATE INDEX symbols_name_idx ON symbols (name);
-- Methods of a class, and the cascade when a class header is deleted.
CREATE INDEX symbols_parent_idx ON symbols (parent_id);

CREATE TABLE edges (
    -- calls / imports / inherits: src uses dst. tested_by: src is the code, dst the test. Imports hang off <module>.
    src_id BIGINT NOT NULL REFERENCES symbols (id) ON DELETE CASCADE,
    dst_id BIGINT NOT NULL REFERENCES symbols (id) ON DELETE CASCADE,
    kind   TEXT NOT NULL CHECK (kind IN ('calls', 'imports', 'inherits', 'tested_by')),
    PRIMARY KEY (src_id, kind, dst_id)
);

-- Callers of a symbol and importers of a module; callees and tests use the primary key.
CREATE INDEX edges_dst_idx ON edges (dst_id, kind);

CREATE TABLE unresolved_calls (
    src_id BIGINT NOT NULL REFERENCES symbols (id) ON DELETE CASCADE,
    -- The call as written: "foo", "self.save", "client.get". External library calls are not stored.
    name   TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('not_found', 'ambiguous', 'dynamic')),
    PRIMARY KEY (src_id, name)
);

CREATE TABLE reviews (
    id                 BIGSERIAL PRIMARY KEY,
    repo_id            BIGINT NOT NULL REFERENCES repos (id) ON DELETE CASCADE,
    target             TEXT NOT NULL CHECK (target ~ '^(pr:[1-9][0-9]*|push:.+)$'),
    base_sha           TEXT,
    head_sha           TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued', 'running', 'posted', 'skipped', 'failed')),
    -- Why it was skipped (initial_import, superseded, docs_only, ...) or why it failed.
    status_reason      TEXT,
    -- Categories whose agent failed; the posted review names them (partial review).
    missing_categories TEXT[] NOT NULL DEFAULT '{}'
                       CHECK (missing_categories <@ ARRAY['security', 'logic', 'quality']),
    llm_calls          INTEGER NOT NULL DEFAULT 0,
    tokens_in          INTEGER NOT NULL DEFAULT 0,
    tokens_out         INTEGER NOT NULL DEFAULT 0,
    latency_ms         INTEGER,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status NOT IN ('skipped', 'failed') OR status_reason IS NOT NULL),
    -- The dedupe key: one review per commit per target.
    UNIQUE (repo_id, target, head_sha)
);

-- Keeps updated_at true on every status change, so stuck 'running' reviews can be found.
CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER reviews_touch_updated_at BEFORE UPDATE ON reviews
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE findings (
    id                BIGSERIAL PRIMARY KEY,
    review_id         BIGINT NOT NULL REFERENCES reviews (id) ON DELETE CASCADE,
    -- Mirrors review/state.py Finding; category is Finding.agent.
    category          TEXT NOT NULL CHECK (category IN ('security', 'logic', 'quality')),
    severity          TEXT NOT NULL CHECK (severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')),
    rule_id           TEXT NOT NULL,
    confidence        TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    file_path         TEXT NOT NULL,
    line_number       INTEGER NOT NULL CHECK (line_number >= 1),
    -- Enclosing symbol in the reviewed code, "<module>" outside any def; no FK because PR code is never indexed.
    qualified_name    TEXT NOT NULL,
    title             TEXT NOT NULL,
    description       TEXT NOT NULL,
    evidence          TEXT NOT NULL DEFAULT '',
    suggestion        TEXT NOT NULL,
    -- sha1(category + file_path + qualified_name + rule_id): survives line shifts.
    fingerprint       TEXT NOT NULL,
    -- Inline PR comment or commit comment id; NULL when the finding is only in the summary.
    github_comment_id BIGINT
);

-- Findings of a review, and the fingerprints already posted on a target (joined from reviews).
CREATE INDEX findings_review_fingerprint_idx ON findings (review_id, fingerprint);
