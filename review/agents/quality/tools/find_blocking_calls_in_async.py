import re
from typing import Iterator

from review.agents.quality.tools.python_source_scan import code_lines, scan_python_files
from review.agents.tools.tool_definition import Tool, ToolContext


# Sync calls that hold the event loop; the lookbehinds skip aiofiles.open( and self.requests.
BLOCKING_CALL_PATTERN = re.compile(
    r"(?<![\w.])requests\.\w+\(|\btime\.sleep\(|\burlopen\(|(?<![\w.])open\("
    r"|\bsubprocess\.(run|call|check_call|check_output)\(|\b(psycopg2|sqlite3|pymysql)\.connect\("
    r"|\.(read_text|write_text|read_bytes|write_bytes)\("
)
# Database calls that only block when they are not awaited (a sync driver or a sync session).
SYNC_DB_CALL_PATTERN = re.compile(r"\.(execute|executemany|fetchone|fetchall|commit)\(")
THREAD_OFFLOAD_MARKERS = ("to_thread(", "run_in_executor(")


def _is_blocking(text: str) -> bool:
    if any(marker in text for marker in THREAD_OFFLOAD_MARKERS):
        return False
    if BLOCKING_CALL_PATTERN.search(text):
        return True
    return "await " not in text and SYNC_DB_CALL_PATTERN.search(text) is not None


# Blocking lines whose innermost enclosing function is an async def; a nested plain def is not async code.
def find_blocking_lines(source: str) -> Iterator[tuple[int, str]]:
    functions: list[tuple[int, int, bool]] = []  # (indentation, line number, is async) of the enclosing defs
    for number, indent, text in code_lines(source):
        while functions and indent <= functions[-1][0]:
            functions.pop()
        if functions and functions[-1][2] and _is_blocking(text):
            yield number, f"{text}  (in async def at line {functions[-1][1]})"
        if text.startswith(("def ", "async def ")):
            functions.append((indent, number, text.startswith("async def ")))


def find_blocking_calls_in_async(context: ToolContext, arguments: dict) -> str:
    hits = scan_python_files(context, str(arguments.get("path_prefix", "")), find_blocking_lines)
    return "\n".join(hits) if hits else "No blocking call found inside an async def."


FIND_BLOCKING_CALLS_IN_ASYNC = Tool(
    name="find_blocking_calls_in_async",
    description=(
        "List production lines inside async def bodies that block the event loop: requests.*, time.sleep, open(), "
        "Path.read_text, subprocess.run, sync DB connect or a non-awaited execute/fetch/commit. Pass a file when the "
        "async def starts outside the diff hunk. Calls hidden behind a helper function are not seen."
    ),
    parameters={
        "type": "object",
        "properties": {"path_prefix": {"type": "string", "description": "optional file or folder, e.g. app/api"}},
    },
    run=find_blocking_calls_in_async,
    requires="repo",
)
