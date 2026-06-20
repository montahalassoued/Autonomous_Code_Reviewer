# RAG

This document explains each of the 8 RAG layers used by the reviewer.

---

## Layer 1 — Clone repo → tree-sitter AST parse → function-level chunks

**Goal:** Convert source code into a structured unit that matches how code review is performed.

**Why function-level chunks:** Review risks and design intents usually live inside functions/methods/helpers rather than arbitrary text spans.

**Output:** Function/class/module representations and extracted boundaries.

---

## Layer 2 — Contextual chunking (prepend 50–100 tokens)

**Goal:** Improve embedding quality by adding symbol/file context before embedding.

**Example context fields:**

- file path, symbol name, symbol type
- brief signature/context
- parent class/module name

**Why it helps:** It reduces embedding ambiguity (e.g., common function names) and makes retrieved chunks more specific.

---

## Layer 3 — AST Knowledge Graph (NetworkX)

**Goal:** Model code entities and relationships so the system can do multi-hop reasoning.

**Graph nodes:** File / Class / Function.

**Graph edges:**

- calls (function→function)
- imports (module→module)
- inherits / contains (class→class)

**Why it matters:** Pure vector search often fails when the relevant evidence is not textually similar but is structurally connected (call chain, inheritance tree, composition).

---

## Layer 4 — Dual storage (Qdrant vectors + NetworkX graph pickle)

**Goal:** Keep both dense retrieval and structural reasoning available.

- Qdrant stores contextual vectors for hybrid retrieval.
- NetworkX graph is persisted for traversal and multi-hop expansion.

---

## Layer 5 — Hybrid retrieval (BM25 0.4 + dense 0.6 → RRF → top 150)

**Goal:** Maximize recall using lexical + semantic signals.

- **BM25 (0.4):** exact matches for identifiers, symbols, constants, error messages.
- **Dense (0.6):** semantic intent matches.

**RRF fusion:** Merges candidate rankings from both retrieval styles.

**Why top 150:** Enough breadth so reranking can focus on precision.

---

## Layer 6 — Graph traversal (multi-hop fn→callers→importers)

**Goal:** Expand candidates using structural context.

Instead of only retrieving “similar text”, the system traverses:

- from a touched function to its callers/callees
- across imports to find the relevant modules

This recovers architectural and systemic issues.

---

## Layer 7 — Reranking (cross-encoder → top 20 → top 5)

**Goal:** Improve precision.

Pipeline:

1. Cross-encoder scores candidates from the top fused set.
2. Select **top 20**.
3. Select **top 5** with **position-aware assembly** (best chunk first+last).

**Why position-aware assembly:** LLMs exhibit recency/bias; assembling the most informative evidence at edges improves grounding.

---

## Layer 8 — Corrective loop (LLM-as-judge; score<0.5 → reformulate; max 3 rounds)

**Goal:** Avoid “thin-context reviews”.

Process:

1. Judge evidence quality for the current review question.
2. If score < 0.5, reformulate queries using missing parent symbols, imports, and callers.
3. Retry retrieval and reranking.
4. Cap at 3 rounds to bound latency.

---

## Net effect

The system produces compact, high-signal evidence snippets that reduce retrieval failures and improve reviewer confidence.
