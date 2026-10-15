---
name: clean-code
description: Write clear, readable Python code with comments that anyone can understand. Use whenever writing new code, editing existing code, or refactoring in this project (review/, rag/, tools/, app/, tests/, eval/).
---

# Clean, readable code

The goal: someone who has never seen this project, even a beginner, can open any file and understand **what** it does and **why** it does it that way.

## 1. Comments are one short line

The code and the names explain *how*. A comment only says *what* a block is for, or *why* it's done that way, in **one line, about 15 words at most**.

- **No comment block at the top of a file.** No `"""..."""` header: the file name and its folder say its role (see the `project-structure` skill). The file starts with its imports.
- **No multi-line `"""..."""` blocks**, and no `Args:` / `Returns:` / `Inputs:` / `Outputs:` sections.
- **Use one-line `#` comments**, above a function or a block whose purpose isn't obvious from its name.
  ```python
  # Read the LLM's JSON array; [] if it can't be parsed.
  def parse_findings(raw_output: str) -> list[dict]:
      # Groq sometimes wraps JSON in ``` fences, so strip them first.
      cleaned = raw_output.strip().strip("`")
  ```

The longer *why* of a design choice (alternatives, trade-offs) goes in `docs/DECISIONS.md` with the `log-decision` skill, not in the code.

## 2. When to write a comment

Write one when the code:
- handles an edge case or a quirk (an API limit, a retry, a malformed LLM output)
- makes a non-obvious choice (a magic number, an order, a performance trick)
- would surprise a reader

Don't comment code that its names already explain. Bad: `# loop over the findings`.

## 3. Keep comments true

A wrong comment is worse than none. When you change code, fix or delete the comment next to it.

## 4. Names say what things are

- Use full, descriptive names: `retrieved_chunks`, not `rc`; `max_retries`, not `n`.
- Functions are verbs: `parse_findings()`, `fetch_pr_diff()`.
- Booleans read like questions: `is_valid`, `has_secret`.
- Constants are UPPER_CASE at the top of the file: `DEFAULT_REVIEW_AGENT_MODEL = "..."`.

## 5. Keep code simple

- One function does one thing. If it needs "and" to describe it, split it.
- Keep functions short (roughly under 40 lines). Pull helpers out with clear names.
- Prefer early returns over deeply nested `if` blocks.
- No clever one-liners when a plain loop is easier to read.
- Use type hints on every function signature.
- Don't leave dead code, commented-out code, or unused imports.

## 6. Handle errors clearly

- Catch specific exceptions, not bare `except:`.
- When you swallow an error on purpose (for example, a malformed LLM response), add a one-line comment saying why that's safe.
- Error messages say what went wrong and what to check: `"GITHUB_TOKEN is not set - add it to .env"`.

## 7. Before finishing

Check the code against these questions:
- Could a new developer understand each function from its name (and its one-line comment, if it has one)?
- Is every comment one line, with no `"""` header at the top of the file?
- Does it match the style of the surrounding files?
