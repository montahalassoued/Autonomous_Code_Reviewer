import os
from github import Github, Auth


# Hidden HTML comment that tags the bot's summary so a retry can find and edit it.
SUMMARY_MARKER = "<!-- acr:summary -->"


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


# Fetches the unified diff for a pull request.
def get_pr_diff(repo_full_name: str, pr_number: int) -> str:
    client = _build_github_client()
    repo = client.get_repo(repo_full_name)
    pull_request = repo.get_pull(pr_number)

    diff_parts = []
    for file in pull_request.get_files():
        if file.patch:  # binary files and some renames have no patch
            diff_parts.append(f"--- {file.filename}\n{file.patch}")

    return "\n\n".join(diff_parts)


# Creates the review summary comment, or edits it if one already exists.
def upsert_summary_comment(repo_full_name: str, pr_number: int, markdown: str) -> None:
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


# Thin alias for upsert_summary_comment(), kept for older callers.
def post_review_comment(repo_full_name: str, pr_number: int, markdown: str) -> None:
    upsert_summary_comment(repo_full_name, pr_number, markdown)
