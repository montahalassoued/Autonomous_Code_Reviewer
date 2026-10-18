# Architecture decisions

Every significant design choice, and why it was made. Add new entries with the `log-decision` skill.

Older entries mention five domain agents, a retriever and Chroma: the reasoning still holds, but the graph now has three agents and no retriever (see the later entries).

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
- Bounded per-agent tools (max 3 calls) over a prepared context only: agents can check guards, callers and tests
- Index mirrors `main` only, PR code is an in-memory overlay: no cleanup, no cross-PR leakage
- Review before index update: the review sees the true "before", a failed review never corrupts the index
- Push coalescing over one review per push: fewer LLM calls, one review of the whole range
- `reviews` table over Azure Table dedupe: dedupe, status and metrics in one row (supersedes the Table claim)
- psycopg 3 + numbered SQL migrations over SQLAlchemy + Alembic: graph joins read best as SQL; seven tables need no ORM
- Drop the legacy semantic retrieval before an A/B eval: it was never measured and known-buggy; agents use diff + tools until the context builder (supersedes "delete only after the eval")
- Schema fixed before its first use over a 002 ALTER migration: `findings` mirrors `Finding`, `symbols.name` for call resolution, dead columns dropped
- Static call resolution that stores only provable edges over bare-name matching or a type checker: no false callers in the context, library calls and locals skipped

**Superseded by the structural-context design (kept for history):**

- voyage-code-2 embeddings, contextual chunking, hybrid BM25+dense with RRF, corrective RAG loop, BAAI/bge-reranker-v2-m3: removed from the code; semantic retrieval may come back only as optional "similar code" (pgvector)
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
and no review was posted. The graph is now `START -> security | logic |
quality -> join_agents -> synthesizer -> END` (the retriever and its
retry loop were removed later with the semantic retrieval). A fixed
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
Chroma was removed since; the id is now used by the call-graph nodes.
Validated in tests/unit/test_ast_parser_identity.py.

## Why the reranker scores context + code over the chunk name

Superseded: the reranker was removed with the semantic retrieval.
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
LLM client planned in Phase 3 (review/llm_client.py), so all LLM calls get
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
logged dropped findings). `review/agents/agent_runner.py` now owns all of it, and an
agent is an `AgentSpec(name, prompt_template)` plus its prompt. It also
validates findings strictly (known severity, positive integer line, non-empty
text), logs what it drops, and reads the model from `REVIEW_AGENT_MODEL` so a
retired Groq model is a config change, not a code change. Each agent module
keeps a `_build_groq_client` name so tests can fake one agent at a time.
Alternatives: a base class per agent (inheritance for what is only data), or
one generic node built from a list of specs (loses a named function per
agent in traces and tests). Validated in tests/unit/test_agent_runner.py.

## Why psycopg 3 with plain SQL migrations over SQLAlchemy and Alembic

The code index is queried with graph-style joins (callers, callees, tests of a
symbol) that read most clearly as SQL, and the schema is small and fixed
(`db/migrations/001_initial_schema.sql`). The Postgres layer is psycopg 3 with
its connection pool (`db/postgres.py`), and migrations are numbered `.sql`
files applied in order by `db/migration_runner.py`, which records them in
`schema_migrations` and holds a Postgres advisory lock so two API replicas
starting together never migrate twice. The API applies them at startup.
Alternatives: SQLAlchemy 2.0 (typed models and a query builder, but more code
and concepts than seven tables need) with Alembic (its main gain, generating
migrations from models, only applies with SQLAlchemy models). The unused
`sqlalchemy`, `asyncpg`, `psycopg2-binary` and `alembic` requirements were
removed. Trade-off: no ORM, so every query is hand-written SQL, kept next to the
code that uses it and covered by tests/integration/test_postgres_dedupe.py.

## Why the reviews table over Azure Table Storage for webhook dedupe

The webhook claimed (repo, head sha, PR) in Azure Table Storage. The target
design already needs a `reviews` row per review (status, cost, context), with
the unique key (repo_id, target, head_sha), so a second store only for dedupe
was redundant. `PostgresDedupeStore` (app/jobs/dedupe.py) inserts a `queued`
review and treats a unique-key conflict as a duplicate; releasing it after a
failed enqueue deletes the row only while it is still `queued`, so a running
or posted review is never forgotten. `DEDUPE_BACKEND` defaults to postgres when
`JOBS_BACKEND=azure`, so a deployed API without `DATABASE_URL` fails at startup
instead of silently deduping in memory per replica. Trade-off: the API now
depends on Postgres being up, and Azure has no Postgres server provisioned yet.
Supersedes the Table Storage part of "Why a durable queue + separate worker".

## Why bounded per-agent tools over a prepared context only

The target design gave tools only to one shared context subagent, and the
three review agents a prepared context. The agents could not check what they
could not see: whether an endpoint is protected by a router-level dependency,
who calls a changed function, whether a test already exists. Each agent now has
a small read-only toolset in its folder (`review/agents/<category>/tools/`,
one file per tool it owns, listed in `toolset.py`): Security `find_auth_guards`
/ `find_input_guards` / `find_security_settings` / `find_sql_queries`, Logic `find_exception_handlers` plus the shared
`find_callers` / `find_callees`, Quality `find_similar_functions` plus the
shared `find_tests`, and `read_file` / `search_code`. The runner allows at most
`max_tool_calls` (3) per agent, then forces an answer with `tool_choice="none"`.
Shared implementations live in `review/agents/tools/`, so path, symlink and
secret-file checks, output caps and nonce wrapping exist in one place, and the
future context subagent reuses the same tools. Alternatives: no agent tools
(cheapest, but the agents guess), an unbounded loop per agent (cost and Groq
tokens-per-minute grow with every turn, less reproducible), every tool copied
into each agent folder (three places to secure). Trade-offs: up to 3 extra
calls per agent per review, each resending the prompt; tool outputs are a
prompt-injection surface (wrapped and marked as data); tools read the checkout
cloned at worker start, not the PR head. Whether tools improve findings is
measured with the benchmark before raising the limit.

## Why drop the legacy semantic retrieval before the structural-vs-legacy eval

The plan kept the semantic pipeline (Gemini context sentences, Voyage embeddings
in Chroma, BM25 + dense with RRF, graph expansion, a local cross-encoder
reranker and an LLM judge loop) alive until an A/B eval showed structural
context doing at least as well. That eval harness was never built, so the
baseline was never measured, and the pipeline had known defects (V3-V7 in
`docs/IMPLEMENTATION_PLAN.md`). Keeping it cost torch and sentence-transformers
in the image, one Gemini call per function at every worker start, up to 6 extra
Groq calls per review, and a second pipeline to maintain. It is removed:
`START` fans out straight to the three agents, which get the diff, a
`review_context` that stays empty until the Phase 4 context builder fills it,
and their bounded tools. `rag/ingestion.py` now only clones, parses with
tree-sitter and builds the NetworkX call graph the graph tools read.
Alternatives: keep the legacy path for the A/B (rigorous, but measures a buggy
baseline and keeps both costs for weeks), or remove only the Gemini chunker
that contradicted D1. Trade-off: the structural context is compared against
"diff + tools" instead of semantic retrieval, so there is no "beats RAG" number.
Supersedes "deleted only after the eval shows structural >= legacy" in "Why
structural context over semantic retrieval".

## Why fix the schema in 001 before its first use over a 002 migration

A data-engineer review of `db/migrations/001_initial_schema.sql` found that it
did not fit the code that will fill it. `findings` had `message` and
`recommendation` instead of the `Finding` fields (no `rule_id`, which the
fingerprint `sha1(category + file_path + qualified_name + rule_id)` needs, no
`confidence`, which the synthesizer reads, no `evidence`), call resolution had
no bare-name column (`qualified_name` is `Class.login` or `login#2`, a call site
only knows `login`), and `repos.embedding_model`, `symbols.norm_hash` and
`reviews.context_symbol_ids` had no reader. 001 was rewritten instead of adding
a 002 of ALTERs, because it existed only in a local, empty database. Choices:
`findings` mirrors `review/state.py` (only `category` stands for `Finding.agent`)
with a CHECK on the three categories, so a fourth agent needs a migration; a
finding has no foreign key to `symbols`, because PR code is never indexed;
`tested_by` edges go from the code to its test; `unresolved_calls` keeps only
`not_found`, `ambiguous` and `dynamic` calls, never external library calls;
`reviews` gets `status_reason` (required when skipped or failed),
`missing_categories` for partial reviews and an `updated_at` trigger.
Trade-off: the fingerprint has no line, so two findings of the same rule in one
function share it and the second is hidden on later commits (worst for the
`other` rule); accepted for stability. `repos.github_id` (survives a repo
rename) is deferred to multi-repo support.

## Why the indexer resolves calls statically and stores only what it can prove

The `edges` table feeds the context builder: a false "caller" wastes the token
budget and misleads the agents, a missing one is listed as "Not verifiable".
`rag/call_resolver.py` therefore links a call only when one target is certain,
in the documented order: `self`/`cls` methods (also on repo base classes), the
imported name (relative imports, aliases, re-exports through `__init__.py`, a
`src/` layout), the same file, then a name unique in the repo. Two candidates
give an `ambiguous` row, none gives `not_found`. Calls into libraries, builtins
and local names (parameters, assigned variables, nested defs) are neither edges
nor rows: they can never be repo symbols, and storing them would flag a "gap" on
nearly every change. A method missing on a class with a base we can't follow
(`Model`, `Generic[T]`) is skipped for the same reason. `obj.method()` on a
variable is not resolved, since its type needs inference. Calls inside a nested
def count for the enclosing function. A file with a syntax
error or not in UTF-8 keeps its `files` row with a `parse_error` and no symbols.
Phase 4 writes a full index (delete the repo's files, cascade, insert) in one
transaction with `indexed_sha` written last; the incremental upsert that keeps
symbol ids is Phase 6. The old `rag/ast_parser.py` + NetworkX graph stays until
the agent tools read Postgres, so both parsers exist for a while. Alternatives:
the bare-name matching of `rag/knowledge_graph.py` (links every same-named
function, V6), or a type checker such as pyright (precise, but a Node runtime
and seconds per file). Trade-off: lower recall on dynamic code, which the
context marks as not verifiable instead of guessing. On this repo: 97 Python
files, 610 symbols, 1106 edges, 3 unresolved calls, 0.3 s.
