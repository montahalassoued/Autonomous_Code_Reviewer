# Architecture

## Overview

The system is organized as five cooperating layers that move from GitHub events to a final review comment on the pull request.

GitHub → FastAPI → LangGraph → RAG Pipeline → GitHub comment

Key principle: keep each layer’s responsibility narrow so retrieval, orchestration, and review formatting can evolve independently.

---

## Layer 1: GitHub Integration

**Input:** GitHub webhook event (PR opened/synchronized/reopened).

**Responsibilities:**

- Verify webhook authenticity using `GITHUB_WEBHOOK_SECRET`.
- Extract `repo_full_name`, `pr_number`, `base_sha`, `head_sha`.
- Fetch PR diff/patch and file metadata via the GitHub API (PyGithub).
- Hand off a normalized review job to the backend (FastAPI → LangGraph).

**Output:** Review job request containing PR metadata and the diff text.

---

## Layer 2: FastAPI Backend

**Input:** Normalized webhook payload.

**Responsibilities:**

- Expose HTTP endpoints for webhook ingestion and manual review triggers.
- Post the review back to GitHub (no separate UI).
- Create the initial `ReviewState` and invoke LangGraph.

**Output:** A running LangGraph execution that produces a final GitHub review artifact.

---

## Layer 3: LangGraph Orchestration

LangGraph is the control plane that routes work between nodes while maintaining structured state.

**State:** A single `ReviewState` object carries:

- PR identity + diff
- retrieved context and retrieval notes
- per-agent findings
- final synthesized review body

**Core flow:**

1. **Supervisor** decides whether retrieval must run (or be repeated) for missing context.
2. **Retriever** performs corrective RAG and writes evidence into state.
3. Five specialist agents run in parallel using the same retrieved evidence.
4. **Synthesizer** merges outputs into a cohesive GitHub Markdown review.
5. The FastAPI layer posts the review back to GitHub.

**Output:** Structured final review (Markdown + line-level comments payload) stored and posted.

---

## Layer 4: RAG Pipeline (8 layers below)

The retrieval subsystem converts “what to review” into “what evidence to read” from the repository.

It is optimized for code review by working at function-level granularity, using an AST-aware knowledge graph, and combining multiple retrieval signals.

---

## Layer 5: Storage & Observability

**Stores:**

- Qdrant Cloud (prod) / ChromaDB (dev) for contextual embeddings.
- PostgreSQL for persisted review/finding records.
- Redis for caching intermediate artifacts and deduplicating work.

**Observability:**

- Langfuse tracing (open source, self-hostable) for debugging agent/tool decisions and evaluating LLM outputs.
- RAGAS evaluation scripts for measuring retrieval quality.

**Output:** Persisted records, traces, and evaluation reports.

---

## 8-Layer RAG Data Flow

1. **Clone repo → AST parse → function-level chunks**
2. **Contextual chunking (prepend 50–100 tokens) for embeddings**
3. **AST Knowledge Graph (NetworkX nodes + edges)**
4. **Dual storage (Qdrant vectors + NetworkX graph pickle)**
5. **Hybrid retrieval (BM25 0.4 + dense 0.6, fused via RRF, top 150)**
6. **Graph traversal (multi-hop callers/importers traversal)**
7. **Cross-encoder reranking (top 20, then top 5 with position-aware assembly)**
8. **Corrective loop (LLM-as-judge; if score<0.5 reformulate, max 3 rounds)**

Together these layers produce compact, high-signal evidence that the specialist agents can directly cite.
