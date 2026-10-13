# DECISIONS.md — architecture decision log

## Why LangGraph over plain sequential Python functions

Explicit state management, conditional routing, true parallel fan-out
(asyncio under the hood), and built-in tracing via LangSmith. A plain
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
Validated in test/test_pipeline_offline.py.

## Why chunk ids are file::qualified_name over file::name

`file::name` made two classes' `__init__` in one file collide (Chroma
rejects duplicate ids in one upsert), and nested defs were indexed
twice. `rag/ast_parser.py` now emits `qualified_name` ("Class.method"),
keeps nested defs inside their parent only, and suffixes a repeated
name in one file ("#2", e.g. a property setter). Graph nodes, Chroma
ids and `all_ids` all use this id; call resolution still uses the
bare name, since a call site only knows `login(...)`. Ingestion
deletes ids no longer produced, so old-format ids don't linger.
Validated in test/test_ast_parser_identity.py.

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
