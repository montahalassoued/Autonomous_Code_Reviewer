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

## What runs today

The semantic pipeline (Gemini context sentences, Voyage embeddings in ChromaDB, LLM query decomposition, hybrid BM25 + dense search with RRF, 1-hop graph expansion, a local cross-encoder reranker and an LLM judge loop) was **removed** before the structural-vs-legacy eval; the reasons are in [DECISIONS.md](DECISIONS.md) ("Why drop the legacy semantic retrieval before the structural-vs-legacy eval").

Until the Phase 4 context builder exists:

1. The worker clones the target repo and parses it with tree-sitter (`rag/ast_parser.py`, via `rag/ingestion.py`). No LLM or embedding call.
2. It builds an in-memory NetworkX call graph (`rag/knowledge_graph.py`), read by the agents' graph tools (`find_callers`, `find_callees`, `find_return_values`, `find_similar_functions`).
3. The agents get the diff, an empty `review_context`, and their bounded read-only tools to look at the checkout and the graph ([AGENTS.md](AGENTS.md)).

**Why similarity was the wrong signal:** a caller that breaks when a signature changes is rarely textually similar to the change, and module-level code (router dependencies, middleware) was not indexed at all.

**What comes next** (see [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), Phase 4): the Postgres index replaces the NetworkX graph (it becomes the `edges` table), the in-memory parse at startup is replaced by the incremental sync, and the context builder fills `review_context`.
