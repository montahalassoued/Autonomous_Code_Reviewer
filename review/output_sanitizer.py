import html
import re
import unicodedata

from review.secret_redactor import redact_secrets
from review.state import Finding


MAX_COMMENT_CHARS = 65_536  # GitHub rejects longer comment bodies with a 422
COMMENT_BUDGET_CHARS = 60_000  # margin for the marker and for GitHub counting UTF-16 units
FIELD_LIMITS_CHARS = {"title": 150, "description": 2_000, "suggestion": 1_500, "evidence": 2_000, "file_path": 200}
MAX_EVIDENCE_LINES = 25
TRUNCATION_NOTE = " … (truncated)"
OVERSIZED_COMMENT = (
    "## 🤖 Autonomous Code Review\n\n"
    "The review was too large to post as one comment. Split the pull request into smaller ones."
)

SAFE_PATH = re.compile(r"[A-Za-z0-9_.\-/ ]{1,200}")
CODE_SPAN = re.compile(r"`[^`\n]{1,200}`")
# Text GitHub would turn into a link, a notification or a cross-reference; shown as inert code instead.
LINKABLE = re.compile(
    r"(?:https?://|www\.)[^\s`<>()\[\]]+"
    r"|[\w.-]+/[\w.-]+@[0-9a-f]{7,40}\b"
    r"|(?<![\w&])(?:[\w.-]+/[\w.-]+)?#\d+\b"
    r"|\bGH-\d+\b"
    r"|(?<![\w`/])@[A-Za-z0-9][A-Za-z0-9-]*(?:/[\w.-]+)?"
)
MARKDOWN_PUNCTUATION = re.compile(r"([\\`*_\[\]|~$])")
# A block marker at the start of a line: heading, quote, list, rule, setext underline, slash command.
LINE_START_MARKER = re.compile(r"^(\s*)([#>+=/-]|\d+[.)])", re.MULTILINE)


def _is_invisible(char: str) -> bool:
    return char not in "\n\t" and unicodedata.category(char) in ("Cf", "Cc")


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", "\n").replace(" ", "\n")


# Drops bidi controls, zero-width and tag characters (Trojan Source, hidden instructions, regex evasion).
def strip_invisible(text: str) -> str:
    return "".join(char for char in _normalize_newlines(text) if not _is_invisible(char))


# Shows invisible characters as <U+202E> so a reviewer sees what the code really contains.
def reveal_invisible(text: str) -> str:
    return "".join(f"<U+{ord(char):04X}>" if _is_invisible(char) else char for char in _normalize_newlines(text))


def truncate_text(text: str, limit_chars: int) -> str:
    return text if len(text) <= limit_chars else text[:limit_chars].rstrip() + TRUNCATION_NOTE


def _truncate_lines(text: str, max_lines: int) -> str:
    lines = text.split("\n")
    return text if len(lines) <= max_lines else "\n".join(lines[:max_lines]) + "\n" + TRUNCATION_NOTE.strip()


# A relative repo path: no absolute path, no "..", no characters that could break out of its code span.
def is_well_formed_repo_path(path: str) -> bool:
    return bool(SAFE_PATH.fullmatch(path)) and not path.startswith("/") and ".." not in path.split("/") and "//" not in path


# Ingest-time cleanup of one finding: secrets redacted, invisible characters dropped, lengths capped; None for a malformed path.
def sanitize_finding(finding: Finding, known_secrets: tuple[str, ...] = ()) -> Finding | None:
    path = strip_invisible(finding["file_path"]).strip()
    if not is_well_formed_repo_path(path):
        return None

    def clean(field: str) -> str:
        return truncate_text(strip_invisible(redact_secrets(finding[field], known_secrets)).strip(), FIELD_LIMITS_CHARS[field])

    evidence = redact_secrets(_normalize_newlines(finding["evidence"]), known_secrets)  # invisible chars kept, revealed at render
    return Finding(
        **{**finding, "file_path": path, "title": clean("title"), "description": clean("description"),
           "suggestion": clean("suggestion"),
           "evidence": truncate_text(_truncate_lines(evidence, MAX_EVIDENCE_LINES), FIELD_LIMITS_CHARS["evidence"])},
    )


def _escape_plain(text: str) -> str:
    return MARKDOWN_PUNCTUATION.sub(r"\\\1", html.escape(text, quote=False))


def _escape_segment(text: str) -> str:
    parts, position = [], 0
    for match in LINKABLE.finditer(text):
        parts.append(_escape_plain(text[position:match.start()]))
        parts.append(f"`{match.group(0)}`")
        position = match.end()
    parts.append(_escape_plain(text[position:]))
    return "".join(parts)


# LLM text as inert Markdown: no HTML, links, images, mentions, references or block markers; `code` spans kept.
def escape_markdown_text(text: str, single_line: bool = False) -> str:
    text = strip_invisible(text)
    if single_line:
        text = " ".join(text.split())
    parts, position = [], 0
    for match in CODE_SPAN.finditer(text):
        parts.append(_escape_segment(text[position:match.start()]))
        parts.append(match.group(0))
        position = match.end()
    parts.append(_escape_segment(text[position:]))
    return LINE_START_MARKER.sub(r"\1\\\2", "".join(parts))


def _longest_backtick_run(text: str) -> int:
    return max((len(run) for run in re.findall(r"`+", text)), default=0)


# A code span no content can close: its backtick fence is longer than any run inside.
def inline_code(text: str) -> str:
    text = " ".join(strip_invisible(text).split())
    fence = "`" * (_longest_backtick_run(text) + 1)
    padded = f" {text} " if text.startswith("`") or text.endswith("`") else text
    return f"{fence}{padded}{fence}"


# A fenced block no content can close, with no info string (no rich renderer, no suggestion block).
def code_block(code: str) -> str:
    code = reveal_invisible(code).rstrip("\n")
    fence = "`" * max(3, _longest_backtick_run(code) + 1)
    return f"{fence}\n{code}\n{fence}"


# Last guard before posting: a body over GitHub's limit is replaced, never sent to fail with a 422.
def fit_comment(body: str, limit_chars: int = MAX_COMMENT_CHARS) -> str:
    return body if len(body) <= limit_chars else OVERSIZED_COMMENT
