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
Why tree-sitter over naive text splitting for the AST parser

Splitting source files by character count (e.g. every 500 chars) can
cut a function definition in half, producing a chunk that is
syntactically broken and semantically meaningless on its own.
tree-sitter parses the actual grammar of the language into a syntax
tree, so we extract exactly the nodes we want (function_definition)
as complete, always-valid units - the chunking boundary is always a
real function boundary, never an arbitrary character offset.
