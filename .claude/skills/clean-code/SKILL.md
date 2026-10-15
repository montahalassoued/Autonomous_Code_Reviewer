---
name: clean-code
description: Write clear, readable Python code with comments that anyone can understand. Use whenever writing new code, editing existing code, or refactoring in this project (agents/, rag/, tools/, app/, tests/, eval/).
---

# Clean, readable code

The goal: someone who has never seen this project, even a beginner, can open any file and understand **what** it does and **why** it does it that way.

## 1. Every file starts with a module docstring

Follow the style already used in the repo (see `rag/reranker.py` and `agents/security.py`):

```python
"""
reranker.py - re-scores search results with a local cross-encoder
and keeps only the top-K chunks.

Inputs: the enriched results from rag/graph_retriever.py.
Outputs: a list of RankedChunk objects that feed agents/retriever.py.

Why this step exists: <one short paragraph explaining the reason,
the alternative that was rejected, and the trade-off>.
"""
```

The docstring covers:
- **What** the file does, in one or two sentences
- **Inputs / Outputs**, and which other files in the repo it connects to
- **Why**: the design decision behind it, if it isn't obvious

## 2. Every public function and class has a docstring

```python
def rerank(query: str, candidates: list[EnrichedResult], top_k: int = 5) -> list[RankedChunk]:
    """
    Score each candidate against the query and return the best top_k.

    Args:
        query: the reviewer's search question.
        candidates: results from hybrid search, usually 15-20 items.
        top_k: how many chunks to keep.

    Returns:
        The top_k chunks, sorted from most to least relevant.
    """
```

## 3. Comments explain *why*, not *what*

- Good: `# Groq sometimes wraps JSON in markdown fences, so strip them first`
- Bad: `# loop over the list`

Add a comment when code:
- handles an edge case or a known bug or quirk (an API limit, a retry, a malformed LLM output)
- makes a non-obvious choice (a magic number, ordering, a performance trick)
- would surprise a reader

Don't comment on code that is already obvious from good names.

## 4. Names say what things are

- Use full, descriptive names: `retrieved_chunks`, not `rc`; `max_retries`, not `n`.
- Functions are verbs: `parse_findings()`, `fetch_pr_diff()`.
- Booleans read like questions: `is_valid`, `has_secret`.
- Constants are UPPER_CASE at the top of the file: `RERANKER_MODEL = "..."`.

## 5. Keep code simple

- One function does one thing. If it needs "and" to describe it, split it.
- Keep functions short (roughly under 40 lines). Pull helpers out with clear names.
- Prefer early returns over deeply nested `if` blocks.
- No clever one-liners when a plain loop is easier to read.
- Use type hints on every function signature.
- Don't leave dead code, commented-out code, or unused imports.

## 6. Handle errors clearly

- Catch specific exceptions, not bare `except:`.
- When you swallow an error on purpose (for example, a malformed LLM response), add a comment saying why that's safe.
- Error messages say what went wrong and what to check: `"GITHUB_TOKEN is not set - add it to .env"`.

## 7. Before finishing

Check the code against these questions:
- Could a new developer understand each function from its name and docstring alone?
- Is every non-obvious line explained by a comment?
- Does it match the style of the surrounding files?
