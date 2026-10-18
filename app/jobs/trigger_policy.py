import os
from dataclasses import dataclass
from typing import Callable


# Whether a pull_request event may start a review, and why not.
@dataclass(frozen=True)
class TriggerDecision:
    allowed: bool
    reason: str = ""


# Repos this server reviews, from ALLOWED_REPOS="owner/a,owner/b" (case-insensitive); empty means none.
def allowed_repos() -> frozenset[str]:
    raw = os.environ.get("ALLOWED_REPOS", "")
    return frozenset(name.strip().lower() for name in raw.split(",") if name.strip())


# Cheapest checks first; the collaborator lookup is a GitHub API call and its errors propagate (fail closed).
def evaluate_pull_request(payload: dict, is_collaborator: Callable[[str, str], bool]) -> TriggerDecision:
    repo_full_name = payload["repository"]["full_name"]
    pull_request = payload["pull_request"]
    repos = allowed_repos()
    if not repos:
        return TriggerDecision(False, "ALLOWED_REPOS is not set, so no repo is reviewed")
    if repo_full_name.lower() not in repos:
        return TriggerDecision(False, f"repo {repo_full_name} is not in ALLOWED_REPOS")
    if pull_request.get("draft"):
        return TriggerDecision(False, "draft pull request")
    author = (pull_request.get("user") or {}).get("login")
    if not author:
        return TriggerDecision(False, "pull request has no author login")
    if not is_collaborator(repo_full_name, author):
        return TriggerDecision(False, f"author {author} is not a collaborator")
    return TriggerDecision(True)
