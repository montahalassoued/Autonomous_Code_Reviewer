import re
import unicodedata
from dataclasses import dataclass


MAX_SIGNALS = 10
EXCERPT_CHARS = 80
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Phrases aimed at an LLM reviewer rather than at a human reader. A hint, not a verdict: the review stays deterministic.
INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("override_instructions", re.compile(
        r"\b(?:ignore|disregard|forget|override)\b.{0,30}\b(?:previous|prior|above|earlier|all|your|system)\b.{0,20}"
        r"\b(?:instructions?|prompts?|rules?|guidelines?)\b", re.IGNORECASE)),
    ("role_change", re.compile(r"\b(?:you are now|from now on you|act as (?:an?|the) (?:ai|assistant|reviewer|llm))\b", re.IGNORECASE)),
    ("addresses_the_reviewer", re.compile(
        r"\b(?:ai|llm|gpt|claude|automated|code)[ -]?(?:reviewers?|assistants?|models?)\b\s*[,:]\s*"
        r"(?:please|you|ignore|do not|don't|never|always|approve|report)", re.IGNORECASE)),
    ("suppress_findings", re.compile(
        r"\b(?:do not|don't|never)\s+(?:report|flag|mention|comment on)\b.{0,40}\b(?:issues?|findings?|vulnerabilit\w*|this)\b",
        re.IGNORECASE)),
    ("forced_verdict", re.compile(
        r"\b(?:approve this|mark (?:this|it) as (?:safe|approved)|respond (?:only )?with|output only|reply with)\b", re.IGNORECASE)),
    ("fake_conversation", re.compile(r"^\W*(?:system|assistant|developer)\s*(?:prompt|message)?\s*:", re.IGNORECASE)),
    ("forged_marker", re.compile(r"<<<\s*(?:END_)?(?:PULL_REQUEST_DIFF|REPOSITORY_CONTEXT|TOOL_OUTPUT)\b")),
)


# One suspicious added line: where it is, which pattern matched, and a short excerpt.
@dataclass(frozen=True)
class InjectionSignal:
    file_path: str
    line_number: int
    kind: str
    excerpt: str


def _has_hidden_characters(line: str) -> bool:
    return any(unicodedata.category(char) == "Cf" for char in line)


def _excerpt(line: str) -> str:
    visible = "".join(char for char in line if unicodedata.category(char) not in ("Cf", "Cc")).strip()
    return visible if len(visible) <= EXCERPT_CHARS else visible[:EXCERPT_CHARS] + "…"


def _kind_of(line: str) -> str | None:
    for kind, pattern in INJECTION_PATTERNS:
        if pattern.search(line):
            return kind
    return "hidden_characters" if _has_hidden_characters(line) else None


# Added lines of a unified diff that look written for the AI reviewer, with their new-file line numbers.
def detect_injection(diff: str) -> list[InjectionSignal]:
    signals: list[InjectionSignal] = []
    file_path, line_number = "", 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            file_path = line[4:].removeprefix("b/")
            continue
        if line.startswith(("diff --git ", "--- ")):
            continue
        hunk = HUNK_HEADER.match(line)
        if hunk:
            line_number = int(hunk.group(1))
            continue
        if line.startswith("+"):
            kind = _kind_of(line[1:])
            if kind:
                signals.append(InjectionSignal(file_path, line_number, kind, _excerpt(line[1:])))
                if len(signals) == MAX_SIGNALS:
                    break
            line_number += 1
        elif line.startswith(" "):
            line_number += 1
    return signals
