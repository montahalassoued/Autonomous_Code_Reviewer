import os
from pathlib import Path
from typing import Callable

from code_index.file_classifier import SKIPPED_DIRS, is_secret_file
from review.agents.tools.tool_definition import Tool, ToolContext


MAX_READ_LINES = 200
MAX_SEARCH_HITS = 40
MAX_FILE_BYTES = 1_000_000
SEARCHED_SUFFIXES = {".py", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".txt", ".md", ".js", ".ts"}


def _require_repo(context: ToolContext) -> Path:
    if context.repo_root is None:
        raise RuntimeError("no repository checkout is available in this review")
    return context.repo_root.resolve()


def _is_inside(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


# Resolve a repo-relative path; refuse escapes (../, absolute paths, symlinks out) and secret files.
def resolve_inside_repo(context: ToolContext, relative_path: str) -> Path:
    root = _require_repo(context)
    path = (root / relative_path).resolve()
    if not _is_inside(root, path) or any(part in SKIPPED_DIRS for part in path.relative_to(root).parts):
        raise ValueError(f"path '{relative_path}' is outside the repository")
    if is_secret_file(path.name):
        raise ValueError(f"'{relative_path}' may contain secrets and cannot be read")
    return path


# Walk the checkout once, pruning skipped folders before descending; symlinks and secrets are never read.
def iter_repo_files(root: Path):
    for directory, subdirectories, file_names in os.walk(root):
        subdirectories[:] = sorted(name for name in subdirectories if name not in SKIPPED_DIRS)
        for name in sorted(file_names):
            path = Path(directory) / name
            if path.is_symlink() or path.suffix not in SEARCHED_SUFFIXES or is_secret_file(path.name):
                continue
            if path.stat().st_size <= MAX_FILE_BYTES:
                yield path


# Lines start_line..end_line of a file, numbered like the diff so the agent can cite them.
def read_file(context: ToolContext, arguments: dict) -> str:
    path = resolve_inside_repo(context, str(arguments["path"]))
    if not path.is_file():
        return f"No such file: {arguments['path']}"
    if path.stat().st_size > MAX_FILE_BYTES:
        return f"{arguments['path']} is larger than {MAX_FILE_BYTES:,} bytes and is not read."
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(arguments.get("start_line", 1)))
    end = min(len(lines), int(arguments.get("end_line", start + MAX_READ_LINES - 1)), start + MAX_READ_LINES - 1)
    numbered = [f"{number}: {lines[number - 1]}" for number in range(start, end + 1)]
    return "\n".join(numbered) if numbered else f"{arguments['path']} has only {len(lines)} lines"


# Lines containing any of the texts, literal (not regex) so a pattern from the model can never hang.
def search_lines(
    context: ToolContext,
    texts: tuple[str, ...],
    path_filter: Callable[[str], bool] | None = None,
    line_filter: Callable[[str], bool] | None = None,
) -> list[str]:
    root = _require_repo(context)
    hits = []
    for path in iter_repo_files(root):
        relative = path.relative_to(root).as_posix()
        if path_filter and not path_filter(relative):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if any(text in line for text in texts) and (line_filter is None or line_filter(line)):
                hits.append(f"{relative}:{number}: {line.strip()}")
                if len(hits) >= MAX_SEARCH_HITS:
                    return hits + [f"... stopped after {MAX_SEARCH_HITS} hits"]
    return hits


# "app/api" matches app/api/... but not app/apix/...
def under_prefix(path_prefix: str) -> Callable[[str], bool]:
    prefix = path_prefix.strip().strip("/")
    return lambda path: not prefix or path == prefix or path.startswith(prefix + "/")


def search_code(context: ToolContext, arguments: dict) -> str:
    text = str(arguments["text"])
    if len(text) < 3:
        return "Search text must be at least 3 characters."
    hits = search_lines(context, (text,))
    return "\n".join(hits) if hits else f"No match for '{text}'."


READ_FILE = Tool(
    name="read_file",
    description="Read lines of a repository file (path relative to the repo root), numbered. Max 200 lines per call.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "e.g. app/api/users.py"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        },
        "required": ["path"],
    },
    run=read_file,
    requires="repo",
)

SEARCH_CODE = Tool(
    name="search_code",
    description="Find lines containing an exact text in the repository (code and config files). Returns file:line: text.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "exact text, e.g. a function or setting name"}},
        "required": ["text"],
    },
    run=search_code,
    requires="repo",
)
