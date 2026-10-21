import os
import re
from typing import Iterable


# Prefix- or keyword-anchored, so git SHAs and hashes are never redacted (no entropy guessing).
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL)),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("github-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("groq-key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    ("langfuse-key", re.compile(r"\bsk-lf-[A-Za-z0-9-]{8,}\b")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("azure-key", re.compile(r"(?<=AccountKey=)[A-Za-z0-9+/=]{40,}")),
    ("jwt", re.compile(r"\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,}")),
    ("url-password", re.compile(r"(?<=://)[^\s:/@]+:[^\s@/]+(?=@)")),
    ("assigned-secret", re.compile(
        r"(?i)(?<![\w])(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)[\w-]*\s*[:=]\s*[\"']?(?!\[REDACTED)([^\s\"']{8,})"
    )),
)

# Guesses from a variable name, not a token format: skipped for what goes to the model, so it can still spot them.
HEURISTIC_SECRET_KINDS = frozenset({"assigned-secret"})

# Environment variables whose values the worker holds; their exact values are redacted wherever they appear.
SECRET_ENV_VARS = ("GROQ_API_KEY", "GITHUB_TOKEN", "GITHUB_WEBHOOK_SECRET", "GOOGLE_API_KEY", "DATABASE_URL")
MIN_KNOWN_VALUE_CHARS = 8


def _replacement(kind: str) -> str:
    return f"[REDACTED:{kind}]"


# Text with every known secret format, and every exact known value, replaced by [REDACTED:<kind>].
def redact_secrets(text: str, known_values: Iterable[str] = (), include_heuristics: bool = True) -> str:
    for value in sorted({value for value in known_values if len(value) >= MIN_KNOWN_VALUE_CHARS}, key=len, reverse=True):
        text = text.replace(value, _replacement("known-secret"))
    for kind, pattern in SECRET_PATTERNS:
        if not include_heuristics and kind in HEURISTIC_SECRET_KINDS:
            continue
        if pattern.groups:
            text = pattern.sub(lambda match, kind=kind: match.group(0).replace(match.group(1), _replacement(kind)), text)
        else:
            text = pattern.sub(_replacement(kind), text)
    return text


def has_secret(text: str) -> bool:
    return any(pattern.search(text) for _kind, pattern in SECRET_PATTERNS)


# What may be sent to the LLM provider: token formats and our own secrets redacted, the name-based guess kept.
def redact_for_llm(text: str) -> str:
    return redact_secrets(text, environment_secret_values(), include_heuristics=False)


# The values of this process's own secret environment variables.
def environment_secret_values() -> tuple[str, ...]:
    return tuple(value for name in SECRET_ENV_VARS if (value := os.environ.get(name, "").strip()))
