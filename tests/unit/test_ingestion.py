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


def test_a_file_in_another_encoding_does_not_stop_the_worker(tmp_path):
    repo = build_test_repo(tmp_path)
    (tmp_path / "legacy.py").write_bytes("# caf\xe9 in Latin-1\ndef old_helper():\n    pass\n".encode("latin-1"))

    graph = ingest_repository(repo)

    assert "legacy.py::old_helper" in graph.nodes and "auth/utils.py::login" in graph.nodes
    print("OK - a Latin-1 file is parsed with replaced bytes instead of crashing startup")


if __name__ == "__main__":
    print("Run under pytest: this test uses the tmp_path fixture.")
