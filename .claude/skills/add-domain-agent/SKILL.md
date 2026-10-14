---
name: add-domain-agent
description: Add, change or remove a review agent (the review categories are security, logic and quality) in the LangGraph review pipeline. Use when the user wants a new kind of review check or a new category, e.g. "add an accessibility agent", or to change what an existing agent looks for.
---

# Add or change a review agent

The review has **three categories by design**: Security, Logic and Quality (see `docs/AGENTS.md`). Before adding a fourth agent, check whether the check fits one of them: a new focus bullet in an existing prompt costs no extra LLM call, while a new agent costs one call per review. Ask the user if unsure, and log a new category with the `log-decision` skill.

All agents share `agents/agent_runner.py`. An agent file holds only its prompt and its `AgentSpec`. Use `agents/logic.py` as the reference. Below, `<name>` is the new agent's lowercase name.

## Step 1: Create `agents/<name>.py`

Copy `agents/logic.py` and change only:

| Part | What to change |
| --- | --- |
| Module docstring | What this agent looks for, Inputs/Outputs, and why it exists as its own category |
| `<NAME>_PROMPT_TEMPLATE` | The role ("You are a senior ...") and the bullet list of what to look for, plus a line saying what the other agents cover so it doesn't report their issues. **Keep** the `{diff}` and `{context_summary}` placeholders and end with `+ FINDINGS_OUTPUT_INSTRUCTIONS` |
| `<NAME>_AGENT` | `AgentSpec(name="<name>", prompt_template=...)` |
| `<name>_node` | Function name and docstring; it calls `run_agent(<NAME>_AGENT, state, _build_groq_client())` |

Never copy parsing, validation or client code into the agent file. If the runner needs a change, change it in `agents/agent_runner.py` so all agents get it.

## Step 2: Wire it into `agents/graph.py`

1. Import it: `from agents.<name> import <name>_node`
2. Add `"<name>"` to `DOMAIN_AGENTS`. This handles the parallel fan-out and the `<name> → join_agents` edge.
3. Register the node: `graph.add_node("<name>", <name>_node)`

The agent must route only to `join_agents`. Never add an edge back to the retriever or the other agents: the graph must stay forward-only so it always terminates.

## Step 3: The synthesizer

Add `"<name>": "<emoji> <Title>"` to `CATEGORY_TITLES` in `agents/synthesizer.py`, in the position its section should appear. Without it, its findings render under "Other".

## Step 4: Tests (follow the `write-tests` skill)

- `tests/unit/test_agent_runner.py`: add the new spec to `test_every_agent_prompt_keeps_placeholders_and_output_contract` and the node to `test_agent_nodes_use_their_own_client_seam`.
- `tests/unit/test_synthesizer.py`: the section order.
- `tests/integration/test_<name>.py`: mirror `tests/integration/test_logic.py`, with one diff that clearly contains an issue for this agent and one safe diff.
- `eval/llm_benchmark.py`: add the prompt to `AGENT_PROMPTS`, and add cases with `"agent": "<name>"` to `eval/dataset/llm_benchmark_cases.json`.

## Step 5: Docs

- `docs/AGENTS.md`: a row in the agents table.
- `CLAUDE.md`: the agent list in the pipeline block.

## Step 6: Verify

```bash
venv/Scripts/python.exe -m pytest tests/unit
venv/Scripts/python.exe -m pytest tests/integration/test_<name>.py -s
```

The second command makes a real Groq call, so give it to the user to run.
