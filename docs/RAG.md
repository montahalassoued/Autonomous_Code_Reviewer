# Retrieval

**File purpose:** what retrieval does in the reviewer, before and after the structural-context redesign.
**Target design:** [ARCHITECTURE.md](ARCHITECTURE.md) (code index, context assembly, context subagent).

## New role of retrieval

Context for a review is **structural**: the changed code plus its callers, callees, tests and module-level code, read from the Postgres code index of `main` and filled into a token budget by a deterministic builder. Nothing is "searched for" by similarity in the core path.

Semantic retrieval is reduced to one optional job: **similar code** (duplicate detection, project conventions), via pgvector embeddings in `symbol_embeddings`, used only when enabled and when the context budget still has room (priority 7 in the context builder).

| Need | Target mechanism |
| --- | --- |
| What the change touches | Symbol diff (`content_hash` / `norm_hash`, matched by `qualified_name`) |
| Who is affected | `edges` (`calls`, `imports`, `inherits`) |
| What tests cover it | `edges` (`tested_by`) |
| Config and non-Python references | grep over `files.content` |
| Gaps the rules can't fill | Bounded context subagent returning symbol ids |
| Similar code (optional) | pgvector over `symbol_embeddings` |

## Current pipeline (legacy, still running)

Until the Phase 4 eval shows structural context doing at least as well, the worker still uses the semantic pipeline:

1. tree-sitter parse into function chunks (`rag/ast_parser.py`)
2. a Gemini context sentence per chunk (`rag/contextual_chunker.py`)
3. a NetworkX call graph, pickled (`rag/knowledge_graph.py`)
4. Voyage embeddings in ChromaDB (`rag/embedder.py`, `db/chroma.py`)
5. LLM query decomposition (`rag/query_decomposer.py`)
6. hybrid BM25 + dense search fused with RRF (`rag/hybrid_search.py`)
7. 1-hop caller/callee expansion (`rag/graph_retriever.py`)
8. cross-encoder reranking (`rag/reranker.py`)
9. an LLM judge that retries retrieval while quality < `QUALITY_THRESHOLD` (`agents/retriever.py`)

**Why it is being replaced:** similarity is the wrong signal for review context. A caller that breaks when a signature changes is rarely textually similar to the change. Module-level code (router dependencies, middleware) is not indexed at all, so the security agent can't see whether an endpoint is protected. The worker also re-ingests the whole repo at every startup, with one Gemini call per function.

**What goes, and when** (see [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)):

- The judge loop, the query decomposer and `graph_retriever` are deleted only after the eval shows structural ≥ legacy.
- The in-memory full re-ingest at startup is replaced by the incremental Postgres sync.
- ChromaDB and the NetworkX pickle are replaced by Postgres (the graph becomes the `edges` table).
