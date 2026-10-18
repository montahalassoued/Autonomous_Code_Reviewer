import os
from github import Github, Auth

from review.output_sanitizer import fit_comment


# Hidden HTML comment that tags the bot's summary so a retry can find and edit it.
SUMMARY_MARKER = "<!-- acr:summary -->"
DIFF_FILE_HEADER = "diff --git "


# Reads GITHUB_TOKEN from the environment.
def _build_github_client() -> Github:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN environment variable is not set. "
            "Create a personal access token at "
            "https://github.com/settings/tokens and set it in your "
            ".env file."
        )
    return Github(auth=Auth.Token(token))


# True when GitHub lists login as a collaborator of the repo (owners and org members with access included).
def is_collaborator(repo_full_name: str, login: str) -> bool:
    repo = _build_github_client().get_repo(repo_full_name, lazy=True)  # lazy: one API call, not two
    return repo.has_in_collaborators(login)


# The pull request's state ("open" or "closed") and current head commit, to drop stale reviews.
def get_pull_request_head(repo_full_name: str, pr_number: int) -> tuple[str, str]:
    pull_request = _build_github_client().get_repo(repo_full_name, lazy=True).get_pull(pr_number)
    return pull_request.state, pull_request.head.sha


# Fetches the unified diff for a pull request.
def get_pr_diff(repo_full_name: str, pr_number: int) -> str:
    client = _build_github_client()
    repo = client.get_repo(repo_full_name)
    pull_request = repo.get_pull(pr_number)

    diff_parts = []
    for file in pull_request.get_files():
        if file.patch:  # binary files and some renames have no patch
            name = file.filename
            diff_parts.append(f"{DIFF_FILE_HEADER}a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n{file.patch}")

    return "\n".join(diff_parts)


# The files of a diff built by get_pr_diff; a patch line never starts with "diff --git".
def changed_files_in_diff(diff: str) -> list[str]:
    return [line.rsplit(" b/", 1)[-1] for line in diff.splitlines() if line.startswith(DIFF_FILE_HEADER)]


# Creates the review summary comment, or edits it if one already exists.
def upsert_summary_comment(repo_full_name: str, pr_number: int, markdown: str) -> None:
    body = fit_comment(markdown if markdown.startswith(SUMMARY_MARKER) else f"{SUMMARY_MARKER}\n{markdown}")
    if not body.startswith(SUMMARY_MARKER):
        body = f"{SUMMARY_MARKER}\n{body}"

    client = _build_github_client()
    repo = client.get_repo(repo_full_name)
    pull_request = repo.get_pull(pr_number)
    bot_login = client.get_user().login

    for comment in pull_request.get_issue_comments():
        # Only a comment that starts with the marker is ours: LLM text quoting it elsewhere never matches.
        if (comment.body or "").startswith(SUMMARY_MARKER + "\n") and comment.user.login == bot_login:
            comment.edit(body)
            return

    pull_request.create_issue_comment(body)
