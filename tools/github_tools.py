"""
github_tools.py - PyGithub wrapper for reading PR diffs and posting
review comments.

Inputs: repo_full_name ("owner/repo"), pr_number, and (for posting) the
final review markdown produced by agents/synthesizer.py.
Outputs: the raw PR diff text (for get_pr_diff) or a posted issue
comment on the PR (for post_review_comment).

Kept as a thin wrapper deliberately: app/api/webhook.py calls
get_pr_diff() to build the initial ReviewState, and (after the graph
finishes) post_review_comment() to publish final_review_markdown. All
GitHub API specifics live here so the rest of the app only deals with
plain strings.
"""

import os
from github import Github, Auth


def _build_github_client() -> Github:
    """
    Reads GITHUB_TOKEN from the environment. Same defensive pattern
    used across the project's LLM clients: fail early with a clear
    message instead of a cryptic error mid-call.

    Returns:
        an authenticated PyGithub client
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN environment variable is not set. "
            "Create a personal access token at "
            "https://github.com/settings/tokens and set it in your "
            ".env file."
        )
    return Github(auth=Auth.Token(token))


def get_pr_diff(repo_full_name: str, pr_number: int) -> str:
    """
    Fetches the unified diff for a pull request.

    Args:
        repo_full_name: the repository in "owner/repo" form
        pr_number: the pull request number

    Returns:
        the PR's diff as a single string, built by concatenating each
        changed file's patch (the same unified-diff hunk format GitHub
        shows in its own UI), prefixed with a file header so the
        diff's file boundaries stay readable downstream (query
        decomposition, domain agents, etc. all parse this text as one
        blob, not as separate per-file inputs).
    """
    client = _build_github_client()
    repo = client.get_repo(repo_full_name)
    pull_request = repo.get_pull(pr_number)

    diff_parts = []
    for file in pull_request.get_files():
        if file.patch:  # binary files and some renames have no patch
            diff_parts.append(f"--- {file.filename}\n{file.patch}")

    return "\n\n".join(diff_parts)


def post_review_comment(repo_full_name: str, pr_number: int, markdown: str) -> None:
    """
    Posts the final review as an issue comment on the pull request.

    Args:
        repo_full_name: the repository in "owner/repo" form
        pr_number: the pull request number
        markdown: the final review markdown produced by
                  agents/synthesizer.py

    Returns:
        None - this is a side-effecting call against the GitHub API.
        A plain issue comment is used (not a formal PR review) since
        the synthesizer produces one consolidated comment rather than
        per-line review comments tied to specific diff positions.
    """
    client = _build_github_client()
    repo = client.get_repo(repo_full_name)
    pull_request = repo.get_pull(pr_number)
    pull_request.create_issue_comment(markdown)
