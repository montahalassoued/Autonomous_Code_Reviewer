## Build order

Step 1 — agents/state.py : define ReviewState TypedDict with all fields
Step 2 — agents/graph.py : create empty StateGraph skeleton
Step 3 — rag/ast_parser.py : parse a local Python repo into functions
Step 4 — rag/contextual_chunker.py : LLM context prepending per chunk
Step 5 — rag/knowledge_graph.py : build NetworkX graph from AST
Step 6 — rag/embedder.py + db/chroma.py : embed and store chunks
Step 7 — rag/hybrid_search.py : BM25 + dense + RRF
Step 8 — rag/graph_retriever.py : multi-hop traversal
Step 9 — rag/reranker.py : cross-encoder reranking
Step 10 — agents/retriever.py : corrective RAG loop in LangGraph
Step 11 — agents/security.py : first agent producing findings
Step 12 — agents/synthesizer.py : format findings as GitHub Markdown
Step 13 — app/webhook.py + tools/github_tools.py : wire GitHub
Step 14 — remaining agents (performance, architecture, testing, documentation)
Step 15 — eval harness (see docs/IMPLEMENTATION_PLAN.md, Phase 1)

## Completed

(empty)

## In progress

Step 1

## Notes

(running notes)
