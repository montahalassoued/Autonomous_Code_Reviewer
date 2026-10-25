import contextvars
import os
import threading
from contextlib import contextmanager
from typing import Iterator

from github import Auth, Github, GithubIntegration


# The GitHub App installation of the job or delivery being handled: set once, read by every GitHub call under it.
_installation: contextvars.ContextVar[int | None] = contextvars.ContextVar("github_installation", default=None)
_clients: dict[int, tuple[Github, Auth.AppInstallationAuth]] = {}
_clients_lock = threading.Lock()
_app_slug: str | None = None


@contextmanager
def github_installation(installation_id: int | None) -> Iterator[None]:
    token = _installation.set(installation_id)
    try:
        yield
    finally:
        _installation.reset(token)


def github_app_configured() -> bool:
    return bool(os.environ.get("GITHUB_APP_ID", "").strip()
                and (os.environ.get("GITHUB_APP_PRIVATE_KEY", "").strip() or os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()))


# The PEM key as text, or from a file; a one-line env value may carry "\n" escapes instead of line breaks.
def _private_key() -> str:
    text = os.environ.get("GITHUB_APP_PRIVATE_KEY", "").strip()
    if text:
        return text.replace("\\n", "\n")
    with open(os.environ["GITHUB_APP_PRIVATE_KEY_PATH"].strip(), encoding="utf-8") as key_file:
        return key_file.read()


def _app_auth() -> Auth.AppAuth:
    return Auth.AppAuth(int(os.environ["GITHUB_APP_ID"].strip()), _private_key())


# One client per installation: its token lasts an hour and PyGithub renews it, so it is minted once, not per call.
def _installation_client(installation_id: int) -> tuple[Github, Auth.AppInstallationAuth]:
    with _clients_lock:
        if installation_id not in _clients:
            auth = _app_auth().get_installation_auth(installation_id)
            _clients[installation_id] = (Github(auth=auth), auth)
        return _clients[installation_id]


# A client for the current job: the App's installation token when the App is configured, else GITHUB_TOKEN.
def github_client() -> Github:
    if github_app_configured():
        installation_id = _installation.get()
        if installation_id is None:
            raise RuntimeError("The GitHub App is configured, but this event carries no installation id: send events "
                               "from the App's webhook, not from a repository webhook.")
        return _installation_client(installation_id)[0]
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Set GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY (a GitHub App), or GITHUB_TOKEN (a personal "
                           "access token from https://github.com/settings/tokens), in your .env file.")
    return Github(auth=Auth.Token(token))


# The token git clones with: the installation's under the App; None lets git use GITHUB_TOKEN (rag/ingestion.py).
def git_token(installation_id: int | None) -> str | None:
    if github_app_configured() and installation_id is not None:
        return _installation_client(installation_id)[1].token
    return None


# Who posts the reviews: "<app-slug>[bot]" under the App (an installation token cannot read /user), else the token's user.
def bot_login(client: Github) -> str:
    global _app_slug
    if not github_app_configured():
        return client.get_user().login
    if _app_slug is None:
        _app_slug = GithubIntegration(auth=_app_auth()).get_app().slug
    return f"{_app_slug}[bot]"


# The worker refuses to start without a way to read repos and post reviews.
def check_github_credentials() -> None:
    if not github_app_configured() and not os.environ.get("GITHUB_TOKEN", "").strip():
        raise RuntimeError("Set GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY (a GitHub App), or GITHUB_TOKEN, "
                           "in your .env file: the worker reads repos and posts reviews with them.")
