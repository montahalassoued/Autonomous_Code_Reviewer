import os

# The agent prompt is ~5,200 tokens before the diff, and Groq's free tier limits tokens per minute per model;
# ~12,000 characters (~3,000 tokens) of diff keeps one agent call under that limit.
DEFAULT_MAX_DIFF_CHARS = 12_000
FILE_HEADER = "diff --git "


def max_diff_chars() -> int:
    value = os.environ.get("REVIEW_MAX_DIFF_CHARS", "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else DEFAULT_MAX_DIFF_CHARS


# The diff cut into (path, text) per file, at each "diff --git" header.
def split_diff_files(diff: str) -> list[tuple[str, str]]:
    files: list[tuple[str, list[str]]] = []
    for line in diff.splitlines():
        if line.startswith(FILE_HEADER):
            files.append((line.rsplit(" b/", 1)[-1], [line]))
        elif files:
            files[-1][1].append(line)
    return [(path, "\n".join(lines)) for path, lines in files]


# Whole files, in diff order, up to the budget; a file that does not fit is skipped and the next ones still tried.
def fit_diff(diff: str, max_chars: int) -> tuple[str, list[str]]:
    kept, dropped, used = [], [], 0
    for path, text in split_diff_files(diff):
        if used + len(text) + 1 > max_chars:
            dropped.append(path)
            continue
        kept.append(text)
        used += len(text) + 1
    return "\n".join(kept), dropped
