import json
import os
import sys

THRESHOLD = 40.0
RED_BOLD = "\033[1;31m"
RESET = "\033[0m"


def ctx_percent(data):
    """Return context usage percent (float) or None if unavailable."""
    cw = data.get("context_window") or {}
    pct = cw.get("used_percentage")
    if isinstance(pct, (int, float)):
        return float(pct)
    used, size = cw.get("total_input_tokens"), cw.get("context_window_size")
    if isinstance(used, (int, float)) and isinstance(size, (int, float)) and size > 0:
        return used / size * 100
    return None


def git_branch(start):
    """Read the branch from .git/HEAD (no subprocess, no git locks)."""
    d = os.path.abspath(start)
    while True:
        git = os.path.join(d, ".git")
        if os.path.isdir(git):
            with open(os.path.join(git, "HEAD"), encoding="utf-8") as f:
                head = f.read().strip()
            prefix = "ref: refs/heads/"
            return head[len(prefix):] if head.startswith(prefix) else head[:7]
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def build(data):
    parts = [str((data.get("model") or {}).get("display_name") or "Claude")]
    pct = ctx_percent(data)
    if pct is None:
        parts.append("ctx: n/a")
    elif pct >= THRESHOLD:
        parts.append(f"{RED_BOLD}⚠ ctx {pct:.0f}% - over 40%: "
                     f"run /context-handoff then /clear{RESET}")
    else:
        parts.append(f"ctx {pct:.0f}%")
    try:
        cwd = (data.get("workspace") or {}).get("current_dir") or data.get("cwd") or os.getcwd()
        branch = git_branch(cwd)
        if branch:
            parts.append(f"git:{branch}")
    except Exception:
        pass
    return " | ".join(parts)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        print(build(json.load(sys.stdin)))
    except Exception:
        print("Claude | ctx: n/a")


if __name__ == "__main__":
    main()
