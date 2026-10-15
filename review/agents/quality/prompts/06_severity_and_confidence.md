Severity - judge the cost at realistic production scale, not the worst imaginable input:
- HIGH: will clearly degrade production as data or traffic grows normally. Examples: an N+1 or a call per row on a request path over a growing table, a blocking call in async def on a request path, a model or pool created per request, an unbounded query or whole-file read whose size the user controls, an outbound call with no timeout on a request path.
- MEDIUM: a real cost with a limit or precondition. Examples: the same patterns in a background job or on data bounded to thousands of items, quadratic work on input of unknown size, a missing index on a new filter, a cache with no bound, a missing test for a fixed bug or for branching logic.
- LOW: a small or local cost, and most maintainability issues: duplicated logic, a function doing several things, a misleading name, missing docs, re.compile of a constant pattern in a loop (re caches compiled patterns, so each iteration costs a lookup).
- INFO: a suggestion with no measurable cost; use it rarely.
- CRITICAL: do not use it for quality findings.

Confidence:
- "high": the costly line and what grows are both visible, e.g. the loop over a query result and the query inside it.
- "medium": the costly line is visible, but the data size or the hot path is inferred from names (a route decorator, a table name).
- "low": it depends on code you cannot see: who calls the function, whether an index, cache or test already exists. Say what you could not see, and call a tool first when one can answer it.
