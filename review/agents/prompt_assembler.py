import json
import re
from pathlib import Path


SHARED_PROMPTS_DIR = Path(__file__).parent / "prompts"
PLACEHOLDERS = ("diff", "context_summary")
PLACEHOLDER_PATTERN = re.compile(r"\{(diff|context_summary)\}")
OTHER_RULE_ID = "other"


# Prompt parts are NN_name.md files read in name order, so adding a prompt is adding a file.
def read_prompt_parts(directory: Path) -> list[str]:
    paths = sorted(directory.glob("*.md"))
    if not paths:
        raise FileNotFoundError(f"No prompt files (NN_name.md) in {directory}")
    return [path.read_text(encoding="utf-8").strip() for path in paths]


def render_rules(rules: dict[str, str]) -> str:
    lines = ['Rule ids: set "rule_id" to the one that fits, or "other" if none does.']
    lines += [f"- {rule_id}: {description}" for rule_id, description in rules.items()]
    return "\n".join(lines)


# Few-shot examples: a diff and the exact JSON the agent should answer with.
def render_examples(examples: list[dict]) -> str:
    blocks = ["Examples of the expected answer (they are not part of the pull request under review):"]
    for number, example in enumerate(examples, start=1):
        lines = [f"Example {number} - {example['about']}", "Diff:", *example["diff"], "Answer:"]
        lines.append(json.dumps(example["answer"], indent=2))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# Agent prompts -> its rules -> its examples -> shared input and output prompts.
def assemble_prompt(agent_dir: Path, rules: dict[str, str]) -> str:
    examples = json.loads((agent_dir / "examples.json").read_text(encoding="utf-8"))
    parts = read_prompt_parts(agent_dir / "prompts")
    parts.append(render_rules(rules))
    parts.append(render_examples(examples))
    parts += read_prompt_parts(SHARED_PROMPTS_DIR)
    template = "\n\n".join(parts)

    for name in PLACEHOLDERS:
        if template.count("{" + name + "}") != 1:
            raise ValueError(f"The assembled prompt for {agent_dir.name} must contain {{{name}}} exactly once")
    return template


# Single pass, so a diff that contains "{context_summary}" is never filled in again.
def fill_prompt(template: str, diff: str, context_summary: str) -> str:
    values = {"diff": diff, "context_summary": context_summary}
    return PLACEHOLDER_PATTERN.sub(lambda match: values[match.group(1)], template)
