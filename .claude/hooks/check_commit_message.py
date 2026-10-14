import json
import re
import sys


# Phrases that mean an attribution trailer slipped into the message.
FORBIDDEN_PATTERNS = [
    r"co-authored-by",
    r"generated with \[?claude",
    r"noreply@anthropic\.com",
]


def block(reason: str) -> None:
    """Print the reason for Claude and exit with code 2, which blocks the tool call."""
    print(f"Commit blocked by .claude/hooks/check_commit_message.py: {reason}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Never break unrelated commands because of an unreadable payload.
        sys.exit(0)

    command = payload.get("tool_input", {}).get("command", "")

    # Only react to a real `git commit` invocation: at the start of the
    # command or right after a separator (; & | newline). This avoids
    # blocking commands that merely mention "git commit" inside a string.
    commit_pattern = r"(?:^|[;&|\n]\s*)git(?:\s+-\S+(?:\s+\S+)?)*\s+commit\b"
    matches = list(re.finditer(commit_pattern, command))
    if not matches:
        sys.exit(0)

    # A chained command can contain several commits ("git commit ... && git
    # add ... && git commit ..."), so check each commit's own segment:
    # from its start up to where the next commit begins.
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(command)
        check_commit_segment(cut_at_command_end(command[match.start():end]))

    sys.exit(0)


def cut_at_command_end(segment: str) -> str:
    """
    Trims the segment to the commit command itself: up to the first &&, ||, ;
    or | that sits outside quotes. Otherwise flags of later commands in a
    chain (e.g. `python -m pytest`) would be mistaken for commit flags.
    A heredoc (<<) is kept, because it belongs to the commit.
    """
    quote = None
    for position, char in enumerate(segment):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in ";|" or segment.startswith("&&", position):
            return segment[:position]
    return segment


def check_commit_segment(segment: str) -> None:
    """Blocks the command if this one commit's message breaks the title-only rule."""
    lowered = segment.lower()
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, lowered):
            block("remove the Co-Authored-By / Claude attribution - commits are a single title line only.")

    # More than one -m flag creates a message body.
    if len(re.findall(r"(?:^|\s)-m\b", segment)) > 1:
        block("use a single -m with only the title line (no body).")

    # A heredoc or a -F file is how multi-line messages usually get passed in.
    if "<<" in segment or re.search(r"(?:^|\s)(-F|--file)\b", segment):
        block("pass the title with a single -m \"type(scope): summary\" instead of a heredoc or file.")


if __name__ == "__main__":
    main()
