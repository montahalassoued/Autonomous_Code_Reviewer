---
name: add-review-agent
description: Add, change or remove a review agent (the review categories are security, logic and quality), or change an agent's prompts, rules, examples or tools. Use when the user wants a new kind of review check or a new category, e.g. "add an accessibility agent", or to change what an existing agent looks for.
---

# Add or change a review agent

The review has **three categories by design**: Security, Logic and Quality (see `docs/AGENTS.md`). Before adding a fourth agent, check whether the check fits one of them: a new prompt file in an existing agent costs no extra LLM call, while a new agent costs one call per review. Ask the user if unsure, and log a new category with the `log-decision` skill.

Every agent is a folder. Use `review/agents/logic/` as the reference. Below, `<name>` is the category's lowercase name.

```text
review/agents/<name>/
  __init__.py      empty
  agent.py         AgentSpec + the LangGraph node <name>_node
  prompts/         01_role.md, 02_<topic>.md, ... read in number order
  rules.py         <NAME>_RULES = {rule_id: short description}
  examples.json    [{"about", "diff": [lines], "answer": [findings]}], one real finding + one trap ([])
  tools/
    __init__.py    empty
    toolset.py     <NAME>_TOOLS = (READ_FILE, ..., <OWN_TOOL>)
    find_<x>.py    one file per tool only this agent uses: the function + its Tool
```

## Change an existing agent

- **What it looks for:** edit or add a file in `prompts/` (`NN_topic.md`). Keep the number order meaningful: role first, scope last.
- **What it may report:** add a rule to `rules.py`. The rules are listed in the prompt automatically, and an unknown `rule_id` from the model becomes `other`.
- **How it answers:** add or fix an example in `examples.json`. Line numbers must be new-file lines from the hunk header. Never reuse a case from `eval/dataset/llm_benchmark_cases.json`, or the benchmark measures memorization.
- **Which tools it may call:** edit `tools/toolset.py`. A tool only this agent needs gets its own file `tools/find_<x>.py` (never several tools in one file), with a test in `tests/unit/test_agent_tools.py`; a tool two agents (or the future context subagent) need goes in `review/agents/tools/`. Every tool is read-only, declares `requires="repo"` or `"graph"`, returns text, and runs through `execute_tool_call` (caps, nonce wrapping, never raises). Never read files outside the checkout or secret files: reuse `resolve_inside_repo` / `search_lines`.
- Never put `{diff}` or `{context_summary}` in an agent's files: they live once, in the shared `review/agents/prompts/90_review_input.md`. `assemble_prompt` fails at import otherwise.

## Add a new agent

1. Copy `review/agents/logic/` to `review/agents/<name>/` and rewrite `prompts/`, `rules.py`, `examples.json`. In `agent.py`, rename the spec (`<NAME>_AGENT`, `name="<name>"`) and the node (`<name>_node`). Its role prompt says what the other agents cover, so issues are not reported twice.
2. Wire it into `review/graph.py` (the orchestrator): import `<name>_node` from `review.agents.<name>.agent`, add `"<name>"` to `REVIEW_AGENTS`, and `graph.add_node("<name>", partial(<name>_node, tool_context=tool_context))` so its tools can reach the checkout and the call graph. It routes only to `join_agents`: the graph stays forward-only.
3. Add `"<name>": "<emoji> <Title>"` to `CATEGORY_TITLES` in `review/synthesizer.py`.
4. Tests (follow the `write-tests` skill):
   - `tests/unit/test_agent_runner.py`: add the spec to `test_every_agent_prompt_keeps_placeholders_and_output_contract` and the node to `test_agent_nodes_use_their_own_client_seam`.
   - `tests/unit/test_pipeline_offline.py` needs nothing: it fakes `review.agents.<name>.agent._build_groq_client` for every name in `REVIEW_AGENTS`.
   - `tests/integration/test_<name>_agent.py`: mirror `tests/integration/test_logic_agent.py`.
   - `eval/llm_benchmark.py`: add the spec's `prompt_template` to `AGENT_PROMPTS`, and cases with `"agent": "<name>"` to the dataset.
5. Docs: a row in `docs/AGENTS.md`, the agent list in `CLAUDE.md`.

## Verify

```bash
venv/Scripts/python.exe -m pytest tests/unit
venv/Scripts/python.exe -m pytest tests/integration/test_<name>_agent.py -s
```

The second command makes a real Groq call, so give it to the user to run.
