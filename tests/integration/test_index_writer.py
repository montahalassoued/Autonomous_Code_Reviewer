import os
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv()

from db.migration_runner import apply_migrations  # noqa: E402 - .env must be loaded before DATABASE_URL is read
from rag.code_indexer import build_index  # noqa: E402
from rag.index_writer import write_index  # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL (docker compose up postgres)")

SERVICE = '''class AuthService:
    def login(self, name):
        return verify(name)


def verify(name):
    return bool(name)
'''

TESTS = '''from app.service import verify


def test_verify():
    assert verify("ada")
'''

CALLERS_SQL = """
SELECT caller.qualified_name FROM edges
JOIN symbols caller ON caller.id = edges.src_id
JOIN symbols callee ON callee.id = edges.dst_id
JOIN files ON files.id = callee.file_id
WHERE files.repo_id = %s AND callee.qualified_name = %s AND edges.kind = %s
"""


@pytest.fixture
def connection():
    import psycopg

    from db.postgres import database_url

    with psycopg.connect(database_url()) as connection:
        apply_migrations(connection)
        connection.commit()
        yield connection


def build_repo(tmp_path) -> str:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "service.py").write_text(SERVICE, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_service.py").write_text(TESTS, encoding="utf-8")
    return str(tmp_path)


def repo_id_of(connection, full_name: str) -> int:
    return connection.execute("SELECT id FROM repos WHERE full_name = %s", (full_name,)).fetchone()[0]


def test_index_is_written_and_queryable(connection, tmp_path):
    repo = f"test/{uuid.uuid4().hex[:12]}"

    stats = write_index(connection, repo, "a" * 40, build_index(build_repo(tmp_path)))
    repo_id = repo_id_of(connection, repo)

    assert connection.execute("SELECT indexed_sha, schema_version FROM repos WHERE id = %s", (repo_id,)).fetchone() == ("a" * 40, 1)
    callers = sorted(connection.execute(CALLERS_SQL, (repo_id, "verify", "calls")).fetchall())
    assert callers == [("AuthService.login",), ("test_verify",)]
    assert connection.execute(CALLERS_SQL.replace("caller.id = edges.src_id", "caller.id = edges.dst_id").replace(
        "callee.id = edges.dst_id", "callee.id = edges.src_id"), (repo_id, "verify", "tested_by")).fetchall() == [("test_verify",)]
    parent = connection.execute(
        "SELECT parent.qualified_name FROM symbols child JOIN symbols parent ON parent.id = child.parent_id "
        "JOIN files ON files.id = child.file_id WHERE files.repo_id = %s AND child.qualified_name = 'AuthService.login'",
        (repo_id,),
    ).fetchone()
    assert parent == ("AuthService",)
    print(f"OK - written to Postgres and queryable: {stats}")


def test_reindexing_the_same_commit_gives_the_same_rows(connection, tmp_path):
    repo = f"test/{uuid.uuid4().hex[:12]}"
    index = build_index(build_repo(tmp_path))

    first = write_index(connection, repo, "a" * 40, index)
    second = write_index(connection, repo, "b" * 40, index)
    repo_id = repo_id_of(connection, repo)
    symbols = connection.execute(
        "SELECT count(*) FROM symbols JOIN files ON files.id = symbols.file_id WHERE files.repo_id = %s", (repo_id,)
    ).fetchone()[0]

    assert first == second and symbols == first.symbols, "a full rebuild replaces rows, it never duplicates them"
    print("OK - a second full index replaces the first one")
