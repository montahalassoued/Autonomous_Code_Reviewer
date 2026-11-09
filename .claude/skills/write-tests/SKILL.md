---
name: write-tests
description: Write pytest tests for this project following its conventions (integration vs unit tests, mocking Groq/GitHub, .env isolation). Use when adding or fixing tests in tests/, or after implementing new code in review/, code_index/, tools/, app/.
---

# Writing tests

Tests live in `tests/unit/` (offline, run in CI) and `tests/integration/` (real APIs, auto-marked `integration` by `tests/conftest.py`, run manually). Name files `test_<module>.py`. Run unit tests with `venv/Scripts/python.exe -m pytest tests/unit`.

## Step 1: Decide which kind of test you need

| Kind | When | Example |
|---|---|---|
| **Unit** (no network) | Logic you own: parsing, formatting, routing, HTTP plumbing, state reducers | `test_state.py`, `test_synthesizer.py`, `test_webhook.py`, `test_main.py` |
| **Integration** (real API) | Checking an LLM or API actually behaves as expected | `test_security_agent.py`, `test_github_tools.py`, `test_postgres_dedupe.py` |

Prefer unit tests. They're fast, free and deterministic. Write an integration test only when the real external behavior is what you're testing. Integration tests use real Groq quota, so tell the user before running them.

## File structure

```python
# imports

# A minimal ReviewState with every field filled in.
def build_state(...) -> dict:
    ...

def test_<behavior_being_checked>():
    ...
    print("OK - <what was verified>")


if __name__ == "__main__":
    test_<...>()
    print("\nAll <module> tests passed.")
```

- Name tests after the **behavior**: `test_webhook_rejects_missing_signature`, not `test_webhook_1`.
- No header comment: the file name `test_<module>.py` says what it tests. That's the `clean-code` skill, applied to tests.
- Use a `build_state()` helper with **every** `ReviewState` field filled in (copy from `tests/integration/test_security_agent.py`).
- Assert messages explain the expectation: `assert len(findings) > 0, "Should detect the SQL injection"`.
- Use `print("OK - ...")` lines. The suite is often run with `-s` to read them.
- The `if __name__ == "__main__":` block lets you run the file directly.

## Unit tests: mocking

- Use pytest's `monkeypatch` fixture to replace network-touching functions where they're **used**, not where they're defined:
  ```python
  monkeypatch.setattr("app.api.webhook.get_pr_diff", lambda repo, pr: FAKE_DIFF)
  ```
- To test an agent without Groq, monkeypatch its module's `_build_groq_client` (e.g. `agents.logic._build_groq_client`) to return a fake object whose `chat.completions.create(...)` returns a canned JSON string. Parsing and validation live in `review/agents/agent_runner.py` (`parse_findings`, `to_finding`); test malformed inputs there: fenced JSON, prose around the array, missing fields, an invalid severity, a non-int `line_number` (see `tests/unit/test_agent_runner.py`).
- Build FastAPI tests with a minimal app plus the router under test and `TestClient` (see `make_app()` in `tests/unit/test_webhook.py`).

## Environment isolation (an important rule)

Other tests call `load_dotenv()`, so the real `.env` can leak into later tests in the same run. When a test depends on an env var value, **force** it at the top of the file before importing the module under test:

```python
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"   # NOT setdefault
```

Or use `monkeypatch.setenv(...)` inside the test.

## Integration tests

- `from dotenv import load_dotenv; load_dotenv()` at the top, before the imports.
- Use a small, obvious input the model can't miss (e.g. a string-concatenated SQL query for security).
- Assert on **shape and invariants** (field types, allowed severities, `agent` name, at least one finding), not exact LLM wording.
- Include one "safe or trivial input doesn't crash" test.

## Before finishing

Run the new file: `venv/Scripts/python.exe -m pytest tests/<unit or integration>/test_<module>.py -s`. Report the real result. If it fails, show the failure.
