import re
from dataclasses import dataclass, field


HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


# The new-side lines of one file in a unified diff: added lines, and every line a review comment may point at.
@dataclass
class FileLines:
    added: set[int] = field(default_factory=set)
    commentable: set[int] = field(default_factory=set)  # added or unchanged context, both on the new side


# Each file of a unified diff with its new-file line numbers, read from the hunk headers.
def map_diff_lines(diff: str) -> dict[str, FileLines]:
    files: dict[str, FileLines] = {}
    current: FileLines | None = None
    line_number = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip().removeprefix("b/")
            current = files.setdefault(path, FileLines()) if path != "/dev/null" else None
            continue
        if line.startswith(("diff --git ", "--- ", "index ")):
            continue
        hunk = HUNK_HEADER.match(line)
        if hunk:
            line_number = int(hunk.group(1))
            continue
        if current is None:
            continue
        if line.startswith("+"):
            current.added.add(line_number)
            current.commentable.add(line_number)
            line_number += 1
        elif line.startswith(" ") or line == "":
            current.commentable.add(line_number)
            line_number += 1
    return files
