import json
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, get_args


logger = logging.getLogger(__name__)

ToolResource = Literal["repo", "graph"]

# Tool results go back into the prompt, so they are capped to keep the token budget bounded.
MAX_TOOL_OUTPUT_CHARS = 4000


# What tools may read during one review: the repo checkout and the call graph, either may be missing.
@dataclass(frozen=True)
class ToolContext:
    repo_root: Path | None = None
    code_graph: Any = None  # networkx.DiGraph from rag/knowledge_graph.py, until the Postgres edges table


# A read-only tool an agent may call: its JSON schema for the model, and the function that runs it.
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[[ToolContext, dict], str]
    requires: ToolResource  # the tool is only offered when the context has this resource

    def __post_init__(self) -> None:
        if self.requires not in get_args(ToolResource):
            raise ValueError(f"Tool {self.name}: requires must be one of {get_args(ToolResource)}, got {self.requires!r}")

    def is_available(self, context: ToolContext) -> bool:
        return (context.repo_root if self.requires == "repo" else context.code_graph) is not None

    def to_groq_schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


# Repo content is untrusted: the nonce stops a repo file from faking the end of the block.
def wrap_untrusted(text: str) -> str:
    nonce = secrets.token_hex(8)
    return f"<<<TOOL_OUTPUT {nonce}>>>\n{text}\n<<<END_TOOL_OUTPUT {nonce}>>>"


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    return text[:MAX_TOOL_OUTPUT_CHARS] + f"\n... (truncated, {len(text) - MAX_TOOL_OUTPUT_CHARS} more characters)"


# Run a tool call from the model; never raises, so a bad call costs one turn, not the review.
def execute_tool_call(tools: dict[str, Tool], name: str, raw_arguments: str, context: ToolContext) -> str:
    tool = tools.get(name)
    if tool is None:
        return wrap_untrusted(f"Error: unknown tool '{name}'. Available: {', '.join(sorted(tools))}.")
    try:
        arguments = json.loads(raw_arguments or "{}")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be a JSON object")
        output = tool.run(context, arguments)
    except Exception as error:  # a tool failure must not crash the review
        logger.warning("Tool %s failed: %s", name, error)
        output = f"Error: {error}"
    return wrap_untrusted(_truncate(output))
