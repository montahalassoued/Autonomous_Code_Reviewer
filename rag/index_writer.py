from dataclasses import dataclass

from rag.code_indexer import CodeIndex


# Bump when the indexer stores something new: a mismatch with repos.schema_version triggers a full rebuild.
INDEXER_SCHEMA_VERSION = 1

# DO UPDATE (a no-op) instead of DO NOTHING, so RETURNING gives the id of an existing repo too.
UPSERT_REPO_SQL = """
INSERT INTO repos (full_name) VALUES (%s)
ON CONFLICT (full_name) DO UPDATE SET full_name = EXCLUDED.full_name
RETURNING id
"""

# The cascade removes the old symbols, edges and unresolved calls; reviews and findings are untouched.
DELETE_FILES_SQL = "DELETE FROM files WHERE repo_id = %s"

INSERT_FILE_SQL = """
INSERT INTO files (repo_id, path, language, content_hash, content, parse_error)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING id
"""

INSERT_SYMBOL_SQL = """
INSERT INTO symbols (file_id, name, qualified_name, kind, parent_id, signature, start_line, end_line, code, content_hash)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id
"""

INSERT_EDGE_SQL = "INSERT INTO edges (src_id, kind, dst_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING"

INSERT_UNRESOLVED_SQL = "INSERT INTO unresolved_calls (src_id, name, reason) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING"

# Written last, so a reader never sees an indexed_sha whose rows are not there yet.
MARK_INDEXED_SQL = "UPDATE repos SET indexed_sha = %s, schema_version = %s WHERE id = %s"


@dataclass(frozen=True)
class IndexStats:
    files: int
    symbols: int
    edges: int
    unresolved: int
    parse_errors: int


# Replaces the repo's whole index with this commit's, in one transaction: a failure leaves the old index intact.
def write_index(connection, repo_full_name: str, commit_sha: str, index: CodeIndex) -> IndexStats:
    symbol_ids: dict[tuple[str, str], int] = {}
    with connection.transaction():
        repo_id = connection.execute(UPSERT_REPO_SQL, (repo_full_name,)).fetchone()[0]
        connection.execute(DELETE_FILES_SQL, (repo_id,))
        for indexed in index.files:
            file_id = connection.execute(INSERT_FILE_SQL, (
                repo_id, indexed.path, indexed.language, indexed.content_hash, indexed.content, indexed.parse_error,
            )).fetchone()[0]
            if indexed.module is None:
                continue
            # The extractor lists parents before their children, so parent ids already exist.
            for symbol in indexed.module.symbols:
                parent_id = symbol_ids[(indexed.path, symbol.parent)] if symbol.parent else None
                symbol_ids[(indexed.path, symbol.qualified_name)] = connection.execute(INSERT_SYMBOL_SQL, (
                    file_id, symbol.name, symbol.qualified_name, symbol.kind, parent_id, symbol.signature,
                    symbol.start_line, symbol.end_line, symbol.code, symbol.content_hash,
                )).fetchone()[0]

        edges = [(symbol_ids[edge.src], edge.kind, symbol_ids[edge.dst]) for edge in index.resolution.edges]
        unresolved = [(symbol_ids[call.src], call.name, call.reason) for call in index.resolution.unresolved]
        with connection.cursor() as cursor:
            cursor.executemany(INSERT_EDGE_SQL, edges)
            cursor.executemany(INSERT_UNRESOLVED_SQL, unresolved)
        connection.execute(MARK_INDEXED_SQL, (commit_sha, INDEXER_SCHEMA_VERSION, repo_id))

    return IndexStats(
        files=len(index.files), symbols=len(symbol_ids), edges=len(edges), unresolved=len(unresolved),
        parse_errors=sum(1 for indexed in index.files if indexed.parse_error),
    )
