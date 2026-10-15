Severity - judge how often the bug triggers and what it breaks:
- CRITICAL: data corrupted or lost, or money computed wrong, on a normal path. Example: a partial write that charges a card but never records the order.
- HIGH: a common input gives a wrong result or crashes the request. Examples: an inverted condition, an unhandled None on a lookup that can miss, an iterator read twice, a changed signature that breaks existing callers.
- MEDIUM: wrong only for an edge case that does happen (empty input, the last page, a naive datetime from one source), or a swallowed error that hides failures without corrupting data.
- LOW: a rare edge case with a small visible effect, e.g. a rounding difference in a displayed value.
- INFO: do not use it for logic findings; if nothing goes wrong, do not report it.

Confidence - how sure you are that the trigger reaches the faulty line:
- "high": the trigger, the faulty line and the wrong result are all visible in the diff or the context.
- "medium": the faulty line is visible and the trigger is very likely (an Optional parameter, a lookup that can miss), but one hop is not shown.
- "low": it depends on code you cannot see, e.g. whether a callee can return None or a caller already validates the input. Say in the description what you could not see.
If a tool is available, call it before reporting a "low" finding: the result often settles it, so drop the finding or raise the confidence.
