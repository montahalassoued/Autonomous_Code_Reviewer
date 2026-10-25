import os
from dataclasses import dataclass
from typing import Callable


# Whether a pull_request event may start a review, and why not.
@dataclass(frozen=True)
class TriggerDecision:
    allowed: bool
    reason: str = ""


def _names(variable: str) -> frozenset[str]:
    return frozenset(name.strip().lower() for name in os.environ.get(variable, "").split(",") if name.strip())


# Single repos this server reviews, from ALLOWED_REPOS="owner/a,owner/b" (case-insensitive).
def allowed_repos() -> frozenset[str]:
    return _names("ALLOWED_REPOS")


# Owners whose every repo this server reviews, from ALLOWED_OWNERS="me,my-org": a new repo needs no setting.
def allowed_owners() -> frozenset[str]:
    return _names("ALLOWED_OWNERS")


# Only these spend LLM calls, even if someone else installs the GitHub App on their own repos.
def repo_refusal(repo_full_name: str) -> str | None:
    owners, repos = allowed_owners(), allowed_repos()
    if not owners and not repos:
        return "neither ALLOWED_OWNERS nor ALLOWED_REPOS is set, so no repo is reviewed"
    if repo_full_name.split("/", 1)[0].lower() in owners or repo_full_name.lower() in repos:
        return None
    return f"repo {repo_full_name} is not in ALLOWED_OWNERS or ALLOWED_REPOS"


# Cheapest checks first; the collaborator lookup is a GitHub API call and its errors propagate (fail closed).
def evaluate_pull_request(payload: dict, is_collaborator: Callable[[str, str], bool]) -> TriggerDecision:
    repo_full_name = payload["repository"]["full_name"]
    pull_request = payload["pull_request"]
    refusal = repo_refusal(repo_full_name)
    if refusal:
        return TriggerDecision(False, refusal)
    if pull_request.get("draft"):
        return TriggerDecision(False, "draft pull request")
    author = (pull_request.get("user") or {}).get("login")
    if not author:
        return TriggerDecision(False, "pull request has no author login")
    if not is_collaborator(repo_full_name, author):
        return TriggerDecision(False, f"author {author} is not a collaborator")
    return TriggerDecision(True)


# Source files the agents review; a push touching only docs, images or lock files costs no LLM call.
CODE_SUFFIXES = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rb", ".php", ".cs", ".rs", ".c", ".cc", ".cpp", ".h",
    ".hpp", ".swift", ".scala", ".sql", ".sh",
)
NO_COMMIT = "0" * 40


def touches_code(paths) -> bool:
    return any(path.lower().endswith(CODE_SUFFIXES) for path in paths)


# Every push to a branch of an allowed repo that adds or changes code; the pusher could write to the repo, so no
# collaborator lookup is needed. Deleted branches, tags and pushes of no new commit are skipped.
def evaluate_push(payload: dict) -> TriggerDecision:
    repo_full_name = payload["repository"]["full_name"]
    refusal = repo_refusal(repo_full_name)
    if refusal:
        return TriggerDecision(False, refusal)
    if payload.get("deleted") or payload.get("after") == NO_COMMIT:
        return TriggerDecision(False, "branch deleted")
    if not str(payload.get("ref", "")).startswith("refs/heads/"):
        return TriggerDecision(False, "not a branch push")
    commits = payload.get("commits") or []
    if not commits:
        return TriggerDecision(False, "no new commit")
    if not touches_code(path for commit in commits for path in [*commit.get("added", []), *commit.get("modified", [])]):
        return TriggerDecision(False, "no code file added or changed")
    return TriggerDecision(True)
