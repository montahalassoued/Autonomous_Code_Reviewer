"""
check_python_file.py - Claude Code PostToolUse hook that checks a Python
file right after Claude edits or writes it.

Inputs: the hook payload as JSON on stdin (tool_input.file_path).
Outputs: exit code 0 if the file is fine (or isn't Python), or exit code
2 with the error on stderr so Claude sees the problem and fixes it
immediately.

What it checks:
1. Syntax, via py_compile (standard library, always available).
2. Lint errors via ruff, only if ruff is installed - so the hook works
   today and gets stricter automatically after `pip install ruff`.

Why not run pytest here: most tests in test/ make real Groq/Gemini/
Voyage calls, which would burn API quota on every single edit. Tests
are run on demand by the test-runner subagent instead.
"""

import json
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path


def fail(message: str) -> None:
    """Print the problem for Claude and exit with code 2 so it gets fixed."""
    print(message, file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    file_path = payload.get("tool_input", {}).get("file_path", "")
    path = Path(file_path)
    if path.suffix != ".py" or not path.is_file():
        sys.exit(0)

    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as error:
        fail(f"Syntax error in {path}:\n{error.msg}")

    ruff = shutil.which("ruff")
    if ruff:
        result = subprocess.run(
            [ruff, "check", "--quiet", str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            fail(f"ruff found problems in {path}:\n{result.stdout}{result.stderr}")

    sys.exit(0)


if __name__ == "__main__":
    main()
