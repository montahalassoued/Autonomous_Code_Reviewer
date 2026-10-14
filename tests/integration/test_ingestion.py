"""
Integration test for rag/ingestion.py

Makes REAL calls to Gemini (contextual_chunker.py) and Voyage AI
(embedder.py), plus local ChromaDB storage and graph construction, to
verify ingest_repository() walks a real directory tree end-to-end and
returns everything review/graph.py's build_graph() needs.

Writes a tiny throwaway 2-file "repo" to a temp directory (pytest's
tmp_path fixture) so this test doesn't depend on any specific repo
being present on disk.
"""

import os
from dotenv import load_dotenv
load_dotenv()

from rag.ingestion import ingest_repository, clone_repository


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


def build_test_repo(tmp_path):
    """Writes a tiny 2-file repo layout under tmp_path, including a
    venv-like directory that must be skipped by ingestion."""
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    (auth_dir / "utils.py").write_text(AUTH_FILE, encoding="utf-8")

    notifications_dir = tmp_path / "notifications"
    notifications_dir.mkdir()
    (notifications_dir / "email.py").write_text(EMAIL_FILE, encoding="utf-8")

    # a vendored dependency directory that should be skipped entirely
    venv_dir = tmp_path / "venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "vendored.py").write_text("def should_be_ignored():\n    pass\n", encoding="utf-8")

    return str(tmp_path)


def test_ingest_repository_end_to_end(tmp_path):
    repo_path = build_test_repo(tmp_path)
    persist_dir = str(tmp_path / "chroma_data")

    collection, all_documents, all_ids, graph = ingest_repository(
        repo_path, persist_directory=persist_dir
    )

    print(f"\nIndexed {len(all_ids)} chunks: {all_ids}")
    print(f"Graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")

    # 4 real functions across the 2 real files; the venv file must be skipped
    assert len(all_ids) == 4
    assert len(all_documents) == 4
    assert all("vendored" not in chunk_id for chunk_id in all_ids), (
        "Files under venv/ must be skipped during ingestion"
    )

    expected_ids = {
        "auth/utils.py::hash_password",
        "auth/utils.py::login",
        "auth/utils.py::create_session",
        "notifications/email.py::send_welcome_email",
    }
    assert set(all_ids) == expected_ids

    # login() calls hash_password() and create_session() - both real,
    # known functions in the same repo, so both must be graph edges
    assert graph.number_of_edges() >= 2

    # the collection must actually be queryable (chunks + embeddings landed)
    stored = collection.get(ids=list(expected_ids))
    assert len(stored["ids"]) == 4

    print("OK - ingest_repository parsed, contextualized, embedded, stored, "
          "and graphed a real (tiny) repo end-to-end, skipping vendored code")


# A tiny, stable public repo - fast to clone, unlikely to ever disappear.
SMALL_PUBLIC_REPO_URL = "https://github.com/octocat/Hello-World.git"


def test_clone_repository_clones_a_real_repo(tmp_path):
    target_dir = str(tmp_path / "cloned_repo")

    result_path = clone_repository(SMALL_PUBLIC_REPO_URL, target_dir)

    assert result_path == target_dir
    assert os.path.isdir(os.path.join(target_dir, ".git"))
    print(f"\nOK - cloned {SMALL_PUBLIC_REPO_URL} into {target_dir}")


def test_clone_repository_pulls_instead_of_recloning(tmp_path):
    target_dir = str(tmp_path / "cloned_repo")

    clone_repository(SMALL_PUBLIC_REPO_URL, target_dir)
    # Calling it again on an existing checkout must pull, not fail or
    # re-clone from scratch - this is the path app/main.py hits on
    # every restart against an already-cloned target repo.
    clone_repository(SMALL_PUBLIC_REPO_URL, target_dir)

    assert os.path.isdir(os.path.join(target_dir, ".git"))
    print("OK - calling clone_repository again on an existing checkout pulls instead of failing")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        from pathlib import Path
        test_ingest_repository_end_to_end(Path(tmp))
    print("\nIngestion integration test passed.")
