import os
from github import Github, Auth


# Hidden HTML comment that tags the bot's summary so a retry can find and edit it.
SUMMARY_MARKER = "<!-- acr:summary -->"


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


def upsert_summary_comment(repo_full_name: str, pr_number: int, markdown: str) -> None:
    """
    Creates the review summary comment, or edits it if one already exists.

    Why: the review job is retried (at-least-once delivery), so posting must
    be idempotent. The body starts with a hidden marker; an existing issue
    comment containing the marker AND written by the authenticated user is
    edited in place. Comments with the marker from other users are ignored,
    so nobody can hijack the slot by pasting the marker.

    Args:
        repo_full_name: the repository in "owner/repo" form
        pr_number: the pull request number
        markdown: the final review markdown produced by review/synthesizer.py

    Returns:
        None - this is a side-effecting call against the GitHub API.
        A plain issue comment is used (not a formal PR review) since the
        synthesizer produces one consolidated comment.

    TODO (Phase 5): inline review comments will need a per-comment
    fingerprint marker for the same idempotency.
    """
    body = markdown if markdown.startswith(SUMMARY_MARKER) else f"{SUMMARY_MARKER}\n{markdown}"

    client = _build_github_client()
    repo = client.get_repo(repo_full_name)
    pull_request = repo.get_pull(pr_number)
    bot_login = client.get_user().login

    for comment in pull_request.get_issue_comments():
        if SUMMARY_MARKER in (comment.body or "") and comment.user.login == bot_login:
            comment.edit(body)
            return

    pull_request.create_issue_comment(body)


def post_review_comment(repo_full_name: str, pr_number: int, markdown: str) -> None:
    """Thin alias for upsert_summary_comment(), kept for older callers."""
    upsert_summary_comment(repo_full_name, pr_number, markdown)
