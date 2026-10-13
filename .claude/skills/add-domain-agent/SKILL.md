---
name: add-domain-agent
description: Add a new domain analysis agent (like security, performance, architecture, testing, documentation) to the LangGraph review pipeline. Use when the user wants a new kind of code review check, e.g. "add an accessibility agent", "add a dependency/license agent".
---

# Add a domain agent

Every domain agent follows the same pattern. Use `agents/security.py` as the reference implementation and copy its structure exactly. Below, `<name>` is the new agent's lowercase name, e.g. `accessibility`.

## Step 1: Create `agents/<name>.py`

Copy `agents/security.py` and change only what is domain-specific:

| Part | What to change |
|---|---|
| Module docstring | What this agent looks for, Inputs/Outputs, and the "same pattern as ..." paragraph |
| `<NAME>_MODEL` | Keep `"llama-3.3-70b-versatile"` unless there's a reason not to |
| `<NAME>_PROMPT_TEMPLATE` | The role ("You are a senior ... engineer") and the bullet list of what to look for. **Keep** the `{diff}` and `{context_summary}` placeholders, the exact JSON field list, and the "Output ONLY a JSON array ... or []" instruction |
| `_format_context` | Change only the docstring's example of why context helps this domain |
| `_to_finding` | `agent="<name>"` |
| `<name>_node` | The function name and docstring |

Keep these unchanged, because they make the pipeline robust:
- `_build_groq_client()`, which fails early with a clear message if `GROQ_API_KEY` is missing
- `_parse_findings()`, which strips code fences, falls back to the outermost `[...]`, and returns `[]` on failure
- `_to_finding()`, which returns `None` for malformed entries instead of raising
- `temperature=0.1`
- returning `{"findings": findings}`, a partial update merged by the `operator.add` reducer

## Step 2: Wire it into `agents/graph.py`

1. Import it: `from agents.<name> import <name>_node`
2. Add `"<name>"` to the `DOMAIN_AGENTS` list. This alone handles the parallel fan-out from `route_after_retriever`, and the `<name> → join_agents` edge in the loop.
3. Register the node: `graph.add_node("<name>", <name>_node)`
4. Update docstrings and comments that say "5 agents" (in `graph.py`, `agents/state.py`, `agents/synthesizer.py`).

The agent must route only to `join_agents`, which goes on to `synthesizer`. Never add an edge from it back to the retriever or the other agents, because the graph must stay forward-only so that it always terminates.

## Step 3: Check the synthesizer

Open `agents/synthesizer.py` and check whether it groups or labels findings by agent name. If so, add the new agent so its findings render with a proper heading.

## Step 4: Add `tests/integration/test_<name>.py` (real Groq call) and offline unit tests in `tests/unit/`

Mirror `tests/integration/test_security.py` (follow the `write-tests` skill):
- `load_dotenv()`, then `build_state(diff)`
- One test with a diff that clearly contains an issue this agent should catch. Assert at least one finding, `agent == "<name>"`, valid severity, `int` line number, and non-empty title, description and suggestion.
- One test with a trivial safe diff. It shouldn't crash and should return a list.

## Step 5: Update the docs

- `docs/AGENTS.md`: add a section in the same format as the others (role, inputs, outputs, tools, prompt summary).
- `README.md`: update the agent list in the opening description.
- If there was a real design choice, add an entry with the `log-decision` skill.

## Step 6: Verify

```bash
venv/Scripts/python.exe -c "from agents.graph import build_graph; print('graph imports OK')"
venv/Scripts/python.exe -m pytest tests/integration/test_<name>.py -s
```

The second command makes a real Groq call, so tell the user before running it.
