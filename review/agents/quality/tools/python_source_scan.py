from typing import Callable, Iterator

from rag.file_classifier import is_test_file
from review.agents.tools.repo_files import MAX_SEARCH_HITS, iter_repo_files, resolve_inside_repo, under_prefix
from review.agents.tools.tool_definition import ToolContext


# Reads one file's source and yields (line number, text to show) for each hit.
LineFinder = Callable[[str], Iterator[tuple[int, str]]]


# Code lines as (number, indentation, stripped text); blank and comment lines never open or close a block.
def code_lines(source: str) -> Iterator[tuple[int, int, str]]:
    for number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield number, len(line) - len(line.lstrip()), stripped


# Run a finder on every production .py file under the prefix, with the same hit cap as search_lines.
def scan_python_files(context: ToolContext, path_prefix: str, find_hits: LineFinder) -> list[str]:
    root = resolve_inside_repo(context, ".")
    in_prefix = under_prefix(path_prefix)
    hits = []
    for path in iter_repo_files(root):
        relative = path.relative_to(root).as_posix()
        if not relative.endswith(".py") or not in_prefix(relative) or is_test_file(relative):
            continue
        for number, text in find_hits(path.read_text(encoding="utf-8", errors="replace")):
            hits.append(f"{relative}:{number}: {text}")
            if len(hits) >= MAX_SEARCH_HITS:
                return hits + [f"... stopped after {MAX_SEARCH_HITS} hits; pass a narrower path_prefix"]
    return hits
