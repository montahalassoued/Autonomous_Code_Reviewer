from rag.ingestion import ingest_repository


AUTH_FILE = '''def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt())


def login(username, password):
    user = db.find_by_username(username)
    if user and hash_password(password) == user.password_hash:
        return create_session(user)
    return None


def create_session(user):
    return {"user_id": user.id}
'''

EMAIL_FILE = '''def send_welcome_email(user):
    return mailer.send(user.email, "Welcome!", "Thanks for joining")
'''


# A tiny 2-file repo plus a venv folder that ingestion must skip.
def build_test_repo(tmp_path) -> str:
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "utils.py").write_text(AUTH_FILE, encoding="utf-8")
    (tmp_path / "notifications").mkdir()
    (tmp_path / "notifications" / "email.py").write_text(EMAIL_FILE, encoding="utf-8")
    (tmp_path / "venv" / "lib").mkdir(parents=True)
    (tmp_path / "venv" / "lib" / "vendored.py").write_text("def should_be_ignored():\n    pass\n", encoding="utf-8")
    return str(tmp_path)


def test_ingest_repository_builds_the_call_graph_offline(tmp_path):
    graph = ingest_repository(build_test_repo(tmp_path))

    assert set(graph.nodes) == {
        "auth/utils.py::hash_password",
        "auth/utils.py::login",
        "auth/utils.py::create_session",
        "notifications/email.py::send_welcome_email",
    }, "every function is a node, and files under venv/ are skipped"
    assert set(graph.successors("auth/utils.py::login")) == {"auth/utils.py::hash_password", "auth/utils.py::create_session"}
    print(f"OK - {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges, no API call")


def test_a_call_links_only_to_the_function_it_imports(tmp_path):
    (tmp_path / "billing").mkdir()
    (tmp_path / "billing" / "format.py").write_text("def render(total):\n    return f'{total:.2f}'\n", encoding="utf-8")
    (tmp_path / "emails").mkdir()
    (tmp_path / "emails" / "format.py").write_text("def render(body):\n    return body.strip()\n", encoding="utf-8")
    (tmp_path / "invoice.py").write_text(
        "from billing.format import render\n\ndef invoice_line(total):\n    return render(total)\n", encoding="utf-8")

    graph = ingest_repository(str(tmp_path))

    assert set(graph.successors("invoice.py::invoice_line")) == {"billing/format.py::render"}, \
        "the bare name 'render' must not link to emails/format.py too"
    print("OK - call edges follow the import, not every function with the same name")


def test_a_file_in_another_encoding_does_not_stop_the_worker(tmp_path):
    repo = build_test_repo(tmp_path)
    (tmp_path / "legacy.py").write_bytes("# caf\xe9 in Latin-1\ndef old_helper():\n    pass\n".encode("latin-1"))

    graph = ingest_repository(repo)

    assert "legacy.py::old_helper" in graph.nodes and "auth/utils.py::login" in graph.nodes
    print("OK - a Latin-1 file is parsed with replaced bytes instead of crashing startup")


def test_a_private_github_repo_is_cloned_with_the_token_in_a_header_not_the_url(monkeypatch, tmp_path):
    import base64

    from rag import ingestion

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    calls = []
    monkeypatch.setattr(ingestion.git.Repo, "clone_from", lambda url, path, **options: calls.append((url, options)))

    ingestion.clone_repository("https://github.com/owner/private.git", str(tmp_path / "checkout"))

    [(url, options)] = calls
    assert url == "https://github.com/owner/private.git" and "ghp_secret" not in url
    expected = base64.b64encode(b"x-access-token:ghp_secret").decode()
    assert options["env"]["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {expected}"
    assert options["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader", "sent to github.com only"
    print("OK - the token reaches git as an environment header, never in the URL or .git/config")


def test_no_token_or_another_host_clones_without_a_header(monkeypatch):
    from rag.ingestion import git_auth_environment

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert git_auth_environment("https://github.com/owner/public.git") == {}
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    assert git_auth_environment("https://gitlab.com/owner/repo.git") == {}, "a GitHub token is never sent elsewhere"
    print("OK - public repos and other hosts get no Authorization header")


if __name__ == "__main__":
    print("Run under pytest: this test uses the tmp_path fixture.")
