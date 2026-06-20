# AGENTS.md

This document describes each agent/entity used by the LangGraph pipeline.

Each agent:

- reads a slice of the shared `ReviewState`
- produces structured findings back into `ReviewState`
- uses retrieval tools only when needed
- keeps outputs evidence-based to make synthesis deterministic

---

## 1) Supervisor

**Role:**

- Decide the next node (retrieval vs. specialist analysis vs. synthesis).
- Determine whether the review can proceed or must loop for additional context.

**Inputs from `ReviewState`:**

- PR diff metadata and changed files
- any already retrieved context
- current status/errors
- per-agent findings if partially completed

**Outputs to `ReviewState`:**

- routing decision / next stage
- retrieval hints (e.g., “also search for callers of X”)
- status updates

**LangChain tools used:**

- State inspection and graph routing primitives (no external retrieval required).

**System prompt summary:**

- Be conservative with uncertainty.
- Prefer evidence-based routing.
- Never skip retrieval when context is thin.

---

## 2) Retriever

**Role:**

- Convert the review needs into retrieval queries.
- Run the corrective RAG loop if the initial evidence is insufficient.

**Inputs from `ReviewState`:**

- PR diff text
- file paths + symbols from the PR
- supervisor hints and retrieval gaps

**Outputs to `ReviewState`:**

- `retrieval_queries`
- `retrieved_context` (ranked snippets + metadata)
- context quality notes and confidence signals

**LangChain tools used:**

- Hybrid search tooling + reranking components wired into a retrieval graph.

**System prompt summary:**

- Search for the exact code that matters.
- Recover from missing/ambiguous evidence.
- Return only context that materially changes the review.

---

## 3) Security

**Role:**

- Identify vulnerabilities and risky patterns.
- Check unsafe data handling, secrets exposure, injection, and authorization flaws.

**Inputs from `ReviewState`:**

- diff text
- retrieved context
- symbol/file metadata

**Outputs to `ReviewState`:**

- `security_findings` (severity, confidence, evidence, remediation)

**LangChain tools used:**

- Code context search from retrieved evidence.

**System prompt summary:**

- Adversarial posture.
- Prefer specific evidence to generic warnings.

---

## 4) Performance

**Role:**

- Detect latency/cost issues and inefficient patterns.
- Look for hot paths, repeated I/O, avoidable work, and scaling concerns.

**Inputs from `ReviewState`:**

- diff text
- retrieved evidence about involved functions/modules

**Outputs to `ReviewState`:**

- `performance_findings` (bottleneck hypotheses, impact, suggestions)

**LangChain tools used:**

- Retrieval-assisted code reasoning and targeted snippet lookup.

**System prompt summary:**

- Look for real bottlenecks.
- Distinguish complexity from cost.

---

## 5) Architecture

**Role:**

- Evaluate modularity and design boundaries.
- Check layering, coupling, and API shape consistency across modules.

**Inputs from `ReviewState`:**

- diff + retrieved architectural context

**Outputs to `ReviewState`:**

- `architecture_findings` (boundary violations, refactor suggestions)

**LangChain tools used:**

- Code search and call-path context derived from graph traversal.

**System prompt summary:**

- Judge fit to system design.
- Call out hidden dependencies and cross-layer leakage.

---

## 6) Testing

**Role:**

- Assess whether the change is adequately tested.
- Identify missing regression coverage and fragile tests.

**Inputs from `ReviewState`:**

- diff
- retrieved context (including relevant tests)

**Outputs to `ReviewState`:**

- `testing_findings` (gaps, recommendations, example tests)

**LangChain tools used:**

- Repository context search for existing tests.

**System prompt summary:**

- Verify behavior changes are exercised.
- Prefer focused regression tests.

---

## 7) Documentation

**Role:**

- Identify drift between behavior and docs.
- Recommend updates for public APIs and operational guidance.

**Inputs from `ReviewState`:**

- diff
- retrieved context about public interfaces/config

**Outputs to `ReviewState`:**

- `documentation_findings` (missing docs, stale references)

**LangChain tools used:**

- Documentation search over repository.

**System prompt summary:**

- Flag setup/usage/supportability changes.

---

## 8) Synthesizer

**Role:**

- Merge all agent findings into a single structured GitHub Markdown review.
- Deduplicate overlaps and order items by severity/confidence.

**Inputs from `ReviewState`:**

- `security_findings`, `performance_findings`, `architecture_findings`, `testing_findings`, `documentation_findings`
- retrieved evidence snippets

**Outputs to `ReviewState`:**

- final review body (Markdown)
- `comments_to_post` payload for GitHub API

**LangChain tools used:**

- None required beyond state inspection.

**System prompt summary:**

- Preserve strongest evidence.
- Avoid redundant phrasing.
- Produce readable, action-oriented Markdown.
