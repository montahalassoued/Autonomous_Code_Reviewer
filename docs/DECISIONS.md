# Architecture decisions

Every significant design choice, and why it was made. Add new entries with the `log-decision` skill.

## At a glance

**Current:**

- Groq over OpenAI: free tier, no credit card. The model is picked with `eval/llm_benchmark.py` (LLaMA 3.3 70B was retired by Groq)
- Three review agents over five: security, logic, quality; adds the missing logic agent, 3 LLM calls instead of 5
- One shared agent runner over a copy per agent: one place for parsing, validation and the model setting
- Langfuse over LangSmith: open source, self-hostable, tracing + evals + prompt versioning, no per-trace cost
- Azure Container Apps webhook server over a GitHub Action: warm worker, multi-repo later, deployment skills
- Durable queue + separate worker over BackgroundTasks: no lost events, retries, poison queue
- Postgres code index over Chroma + NetworkX pickle: one store for symbols, edges, reviews and findings, incremental updates
- Structural context over semantic retrieval: review relevance comes from calls, tests and module code, not similarity
- Bounded context subagent returning ids over free-form summaries: exact code, capped cost, safe fallback
- Index mirrors `main` only, PR code is an in-memory overlay: no cleanup, no cross-PR leakage
- Review before index update: the review sees the true "before", a failed review never corrupts the index
- Push coalescing over one review per push: fewer LLM calls, one review of the whole range
- `reviews` table over Azure Table dedupe: dedupe, status and metrics in one row (supersedes the Table claim)
- BAAI/bge-reranker-v2-m3: CPU-only, no API cost (legacy path; kept only while the legacy mode exists)

**Superseded by the structural-context design (kept for history):**

- voyage-code-2 embeddings, contextual chunking, hybrid BM25+dense with RRF, corrective RAG loop: semantic retrieval is now optional "similar code" only
- NetworkX over Neo4j, ChromaDB dev / Qdrant prod: replaced by the Postgres index (`edges` table)
- Railway over DigitalOcean: replaced by Azure Container Apps

**Open decisions resolved (2026-10-14, see `docs/IMPLEMENTATION_PLAN.md`):**

- D1: embeddings kept only for optional "similar code" (option B)
- D2: on new commits, post only new findings, matched by fingerprint (option a)
- D3: dedupe key is (repo, target, head_sha), the `reviews` unique constraint
- D4: custom deterministic metrics only, no RAGAS

---

## Why LangGraph over plain sequential Python functions

Explicit state management, conditional routing, true parallel fan-out
(asyncio under the hood), and tracing hooks (Langfuse, see below). A plain
function chain would execute the 5 domain agents sequentially (total
time = sum of all 5), while LangGraph runs them concurrently (total
time = the slowest of the 5).

## Why a single shared ReviewState instead of per-agent state objects

Agents need visibility into each other's output (the synthesizer must
read findings from all 5 domain agents). A shared state also lets
LangGraph's conditional edges make routing decisions by simply
inspecting state fields, without extra coordination logic.

## Why operator.add as the reducer for `findings`

5 domain agents write to `findings` in parallel. Without a reducer,
LangGraph's default behavior is "last write wins", which would silently
drop findings from every agent except the last one to finish.
operator.add concatenates the lists instead, so every agent's output
survives regardless of finish order. Validated in test_state.py.

## Why a dedicated `join_agents` barrier node

Initial design had each of the 5 domain agents pointing directly back
to `supervisor`. Since 5 independent edges converge on the same node,
LangGraph re-ran `supervisor` (and consequently `synthesizer`) once per
finished agent instead of once per batch - harmless with stub nodes,
but would have meant paying for 5x the synthesizer LLM calls in
production. Routing all 5 agents to a single intermediate barrier node
that has no other incoming edges guarantees it (and anything
downstream of it) runs exactly once per parallel batch, since
LangGraph waits for all active branches before executing a node they
all share as their only target.

## LangGraph API constraint: conditional fan-out requires no mapping dict

`add_conditional_edges(node, routing_fn)` can be called two ways:
either with a third argument (a dict mapping abstract return keys to
node names), or without it (the routing function must then return
real node names directly). Mixing the two - returning a list of node
names while still passing a mapping dict - raises
`TypeError: unhashable type: 'list'` at compile time, because dict
lookups require hashable values. For true parallel fan-out (returning
a list of node names), the mapping dict must be omitted entirely.

## Why tree-sitter over naive text splitting for the AST parser

Splitting source files by character count (e.g. every 500 chars) can
cut a function definition in half, producing a chunk that is
syntactically broken and semantically meaningless on its own.
tree-sitter parses the actual grammar of the language into a syntax
tree, so we extract exactly the nodes we want (function_definition)
as complete, always-valid units - the chunking boundary is always a
real function boundary, never an arbitrary character offset.

## Why a forward-only graph over a supervisor routing node

The no-op `supervisor` ran after `join_agents` and sent the graph back
to the agents whenever `findings` was empty, so a clean PR looped
until LangGraph's recursion limit (re-calling all 5 agents each time)
and no review was posted. The graph is now `START -> retriever (retry
while score < QUALITY_THRESHOLD and attempts < MAX_RETRIEVAL_ATTEMPTS)
-> 5 agents -> join_agents -> synthesizer -> END`, with both limits
read from `agents/retriever.py` instead of being hardcoded. A fixed
supervisor was the alternative, but it had no real decision to make.
Validated in tests/unit/test_pipeline_offline.py.

## Why chunk ids are file::qualified_name over file::name

`file::name` made two classes' `__init__` in one file collide (Chroma
rejects duplicate ids in one upsert), and nested defs were indexed
twice. `rag/ast_parser.py` now emits `qualified_name` ("Class.method"),
keeps nested defs inside their parent only, and suffixes a repeated
name in one file ("#2", e.g. a property setter). Graph nodes, Chroma
ids and `all_ids` all use this id; call resolution still uses the
bare name, since a call site only knows `login(...)`. Ingestion
deletes ids no longer produced, so old-format ids don't linger.
Validated in tests/unit/test_ast_parser_identity.py.

## Why the reranker scores context + code over the chunk name

Scoring only `"file::function_name"` gave the cross-encoder nothing to
judge relevance on. It now scores the context sentence plus the code
(capped at `MAX_RERANK_CODE_CHARS = 2000`, since the model truncates
long inputs anyway). The position-aware duplicate of the best chunk
was also removed: with 5 chunks it put the same code into every agent
prompt twice. Whether this improves retrieval quality has not been
measured yet (Phase 1 eval).

## Why Langfuse over LangSmith for tracing and LLM evaluation

We need traces of every LLM call (prompts, outputs, tokens, latency, cost),
plus evaluation of review quality across prompt and model changes. LangSmith is
a paid SaaS beyond its free tier and is closed source. Langfuse is open source
(MIT), so it can be self-hosted (no per-trace cost, data stays with us) or used
via its managed cloud, and it covers tracing, prompt versioning, datasets and
scores in one tool. LangSmith was only declared in requirements and config, with
no tracing code yet, so switching costs nothing. Integration point: the shared
LLM client planned in Phase 3 (agents/llm_client.py), so all LLM calls get
traced in one place. The eval harness (Phase 1) can push its scores to Langfuse.
Trade-off: self-hosting adds infrastructure to run, so start on Langfuse Cloud
and self-host later if needed.

## Why a webhook server (Azure Container Apps) over a GitHub Action

Both deliver the same user experience: everything happens in GitHub. The user
pushes or opens a PR, and the review appears as inline comments plus a summary.
There is no web UI (the Streamlit dashboard was removed). A GitHub Action would
need no server and cost nothing to host, but every run starts cold (installing
deps, restoring the index from cache) and each repo needs its own workflow file.
A webhook server keeps the index warm in memory for fast reviews, and can later
serve many repos as a GitHub App. It is also the goal of the project: a real
product and portfolio piece showing Docker, Azure, CI/CD and webhook
engineering. Trade-off: a container runs 24/7 (min replicas = 1) and costs money
even when idle. The review core (RAG + agents + GitHub posting) is independent
of the trigger, so an Action-based trigger could still be added later.

## Why a durable queue + separate worker for reviews

The webhook used to run the review in a FastAPI BackgroundTask. GitHub got its 2xx,
but a restart, deploy, or scale-down mid-review lost the event with no retry, and a
redelivery posted a second comment. Now the webhook only verifies, dedupes and
enqueues a ReviewJob (Azure Storage Queue, 202 response); a separate worker
(`python -m app.worker`, same image, own Container App) ingests the repo once,
builds the graph once, and processes one job at a time. A job is deleted only after
it succeeds; a crash makes it reappear after the visibility timeout (900 s), and
after 3 attempts (dequeue_count) it moves to the `<queue>-poison` queue. Dedupe is a
claim on (repo, head sha, PR) in Azure Table Storage (atomic insert, shared by all
API replicas), released if the enqueue fails so GitHub's redelivery works. Delivery
is at-least-once, so posting is idempotent: one summary comment per PR, found by a
hidden marker and the bot's login, edited instead of duplicated.
Alternatives: Redis (a new service to run, persistence needs care), Service Bus
(more features than needed, higher cost), keeping BackgroundTasks (loses events).
Trade-offs: one more Container App and a storage account to operate; Table Storage
has no TTL so dedupe rows accumulate (tiny); a review that exceeds the visibility
timeout can run twice (the upsert makes the result the same); a slow or stale review
of an old commit can still overwrite a newer one (stale-SHA check not done yet).
Local dev and offline tests use in-memory backends (JOBS_BACKEND=memory).
Validated in tests/unit/test_webhook.py, tests/unit/test_worker.py, tests/unit/test_job_queue.py, tests/unit/test_dedupe.py.

## Why Postgres as code index over Chroma + NetworkX

The index was a Chroma collection plus a pickled NetworkX graph, rebuilt in
memory at every worker start with one Gemini call per function, and with no
place for review history. The target is one Postgres database: `repos`,
`files`, `symbols`, `edges` (the call/import/inherit/tested_by graph),
`unresolved_calls`, optional `symbol_embeddings` (pgvector), `reviews` and
`findings` (schema in `docs/ARCHITECTURE.md`). It is a derived map of `main`;
git stays the source of truth and raw Python files are never stored.
Alternatives: keep Chroma + pickle (no transactions, no incremental deletes,
the graph lives in one process), Neo4j (a second database to run for one-hop
queries that SQL does fine), Qdrant (vectors only). Trade-off: a managed
Postgres to pay for and operate, and graph traversals become SQL joins
(NetworkX is still loaded ad hoc if an algorithm needs it). Hosting is not
decided yet.

## Why structural context over semantic retrieval

A review needs what the change affects: callers (especially of a changed
signature or a deleted function), callees, tests, and module-level code such
as router dependencies. Those are structural facts. Similarity search found
code that *looks like* the diff, ran 2-6 LLM calls per review (decomposer +
judge loop), and never indexed module-level code, so the security agent could
not see whether an endpoint was protected. Context is now built
deterministically from the index with a priority order and a ~12k token
budget, each block tagged with the reason it was included. Alternatives: keep
hybrid search as the core and add graph hops (still similarity-ranked), or
send whole files (blows the budget, dilutes attention). Trade-off: code linked
only by dynamic dispatch or strings is missed; it is listed under "Not
verifiable" and can be recovered by the context subagent or by grep. Legacy
retrieval is deleted only after the eval shows structural >= legacy.

## Why a bounded context subagent returning ids, not summaries

Rules can't close every gap (unresolved calls, a deleted public symbol, a
change across several modules). An agent with tools can, but a free-form
agent is unbounded in cost and its paraphrased code can be wrong or
injected. The subagent runs only when such gaps exist, once for all 3 review
agents, with read-only tools over Postgres and at most 6 tool calls. It
returns `{selected: [{id, reason}], gaps: [...]}`; the deterministic builder
fetches the exact code by id. Invalid output is ignored. Alternatives: no
agent (gaps stay open), one agent per reviewer (3x the calls), letting it
write summaries (unverifiable). Trade-off: one or two extra LLM calls on
complex changes, and it lands only after the structural-vs-legacy eval.

## Why the index mirrors main only, with PR code as an in-memory overlay

If PR code were written to the index, every open PR would need its own
version of the data, cleanup on close, and a guarantee that one PR's code
never leaks into another's review. The index holds `main` at `indexed_sha`
only. For a review, the new code is parsed from git and its edges are
computed on the fly in an in-memory overlay that is thrown away afterwards.
Alternatives: per-PR namespaces in the index (storage and cleanup), or a full
re-index per review (slow, costly). Trade-off: overlay edges are recomputed
every review (cheap: only the changed files). See the open point on the PR
"before" side in `docs/ARCHITECTURE.md`.

## Why review before index update

On a push to `main`, the review needs the old code as its "before" side and
the old callers as context. Updating the index first would erase that.
The worker reviews, posts, then applies the symbol diff in one transaction
and sets `indexed_sha = after`. Before each push it checks
`indexed_sha == before` and resyncs if not, so a failed or skipped update is
repaired by the next push. Alternatives: update first and keep a copy of the
old rows (more storage and code), or update in parallel (race on the same
rows). Trade-off: the index lags `main` by one review; a PR reviewed during
that window sees slightly older context.

## Why push coalescing

Several pushes within seconds (fixup commits, a rebase) would each trigger a
full review, costing LLM calls and posting comments on commits that are
already outdated. Jobs are enqueued with a ~60 s visibility delay, and the
latest push reviews the whole range. Alternatives: review every push (cost,
noise), or debounce in the API process (lost on restart, not shared across
replicas). Trade-off: every review starts at least ~60 s after the push. How
the worker detects and skips a superseded job is still to be designed
(open point in `docs/ARCHITECTURE.md`); the Storage Queue cannot replace a
queued message, so it will rely on the `reviews` table.

## Why three review agents (security, logic, quality) over five

The five agents (security, performance, architecture, testing, documentation)
cost five LLM calls per review, mostly produced few findings, and had no agent
for plain correctness bugs (wrong conditions, off-by-one, unhandled None),
which are what a reviewer looks for first. The review now has three
categories: Security, Logic (correctness, broken contracts for callers) and
Quality (performance, maintainability, missing tests and docs). Each prompt
says what the other two cover, so an issue is reported once. The PR comment
has one section per category, always in that order and shown even when
empty, so the reader sees that all three ran. Alternatives: keep five
(cost, no logic agent), one agent for everything (one prompt doing three
jobs, harder to evaluate per category). Trade-off: the quality prompt covers
four former specialties, so each gets less prompt space; the benchmark
measures it per category.

## Why one shared agent runner over a copy per agent

Each agent copied ~150 lines of client, context formatting, JSON parsing and
validation, and fixes didn't reach every copy (none validated severity or
logged dropped findings). `agents/agent_runner.py` now owns all of it, and an
agent is an `AgentSpec(name, prompt_template)` plus its prompt. It also
validates findings strictly (known severity, positive integer line, non-empty
text), logs what it drops, and reads the model from `REVIEW_AGENT_MODEL` so a
retired Groq model is a config change, not a code change. Each agent module
keeps a `_build_groq_client` name so tests can fake one agent at a time.
Alternatives: a base class per agent (inheritance for what is only data), or
one generic node built from a list of specs (loses a named function per
agent in traces and tests). Validated in tests/unit/test_agent_runner.py.
