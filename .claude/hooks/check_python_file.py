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
