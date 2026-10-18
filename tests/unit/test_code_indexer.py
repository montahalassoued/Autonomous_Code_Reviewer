from contextlib import contextmanager

from rag.code_indexer import build_index
from rag.index_writer import INDEXER_SCHEMA_VERSION, write_index


SERVICE = '''def login(name):
    return make_token(name)


def make_token(name):
    return name
'''

TESTS = '''from app.service import login


def test_login():
    assert login("ada")
'''


# A tiny repo with Python, config, a secret, a binary, a broken file and a venv the indexer must skip.
def build_repo(tmp_path) -> str:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "service.py").write_text(SERVICE, encoding="utf-8")
    (tmp_path / "app" / "broken.py").write_text("def f(:\n", encoding="utf-8")
    (tmp_path / "app" / "latin.py").write_bytes("x = 'caf\xe9'\n".encode("latin-1"))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_service.py").write_text(TESTS, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (tmp_path / ".env").write_text("GROQ_API_KEY=secret\n", encoding="utf-8")
    (tmp_path / "logo.json").write_bytes(b'{"a": "\x00"}')
    (tmp_path / "venv" / "lib").mkdir(parents=True)
    (tmp_path / "venv" / "lib" / "vendored.py").write_text("def ignored():\n    pass\n", encoding="utf-8")
    return str(tmp_path)


def test_build_index_keeps_code_and_text_and_skips_the_rest(tmp_path):
    index = build_index(build_repo(tmp_path))
    files = {indexed.path: indexed for indexed in index.files}

    assert set(files) == {"app/service.py", "app/broken.py", "app/latin.py", "tests/test_service.py", "pyproject.toml"}
    assert files["pyproject.toml"].content.startswith("[project]") and files["app/service.py"].content is None
    assert files["app/broken.py"].parse_error.startswith("syntax error")
    assert files["app/latin.py"].parse_error == "not UTF-8"
    print("OK - secrets, binaries and venv skipped; unparsable files kept with a parse_error")


def test_build_index_resolves_calls_and_tests(tmp_path):
    edges = {(edge.src, edge.kind, edge.dst) for edge in build_index(build_repo(tmp_path)).resolution.edges}

    login = ("app/service.py", "login")
    assert (login, "calls", ("app/service.py", "make_token")) in edges
    assert (login, "tested_by", ("tests/test_service.py", "test_login")) in edges
    print("OK - edges are resolved across the whole checkout")


# Records statements; every INSERT ... RETURNING id gets the next id.
class FakeConnection:

    def __init__(self) -> None:
        self.statements: list[tuple[str, object]] = []
        self.in_transaction = False
        self.next_id = 0

    @contextmanager
    def transaction(self):
        self.in_transaction = True
        yield
        self.in_transaction = False

    def execute(self, sql: str, params=None):
        assert self.in_transaction, "every write happens inside the transaction"
        self.statements.append((sql.split()[0], sql, params))
        self.next_id += 1
        row = (self.next_id,)
        return type("Result", (), {"fetchone": lambda _self: row})()

    @contextmanager
    def cursor(self):
        connection = self

        class Cursor:
            def executemany(self, sql, rows):
                connection.statements.append(("EXECUTEMANY", sql, list(rows)))

        yield Cursor()


def test_write_index_replaces_the_repo_index_and_marks_the_commit_last(tmp_path):
    connection = FakeConnection()

    stats = write_index(connection, "owner/demo", "abc123", build_index(build_repo(tmp_path)))

    sqls = [sql for _kind, sql, _params in connection.statements]
    assert "DELETE FROM files" in sqls[1], "the old index is removed first (cascade)"
    assert sqls[-1].startswith("UPDATE repos SET indexed_sha"), "indexed_sha is written last"
    assert connection.statements[-1][2] == ("abc123", INDEXER_SCHEMA_VERSION, 1)
    edge_rows = next(params for kind, sql, params in connection.statements if kind == "EXECUTEMANY" and "edges" in sql)
    assert len(edge_rows) == stats.edges and all(isinstance(src, int) for src, _kind, _dst in edge_rows)
    assert (stats.files, stats.parse_errors) == (5, 2)
    print(f"OK - one transaction, delete then insert, indexed_sha last: {stats}")


def test_symbol_parents_are_inserted_before_their_methods(tmp_path):
    (tmp_path / "shop.py").write_text("class Cart:\n    def total(self):\n        return 0\n", encoding="utf-8")
    connection = FakeConnection()

    write_index(connection, "owner/demo", "abc123", build_index(str(tmp_path)))

    symbol_rows = [params for _kind, sql, params in connection.statements if "INTO symbols" in sql]
    ids = {row[2]: index for index, row in enumerate(symbol_rows)}
    cart_insert = next(i for i, (kind, sql, params) in enumerate(connection.statements) if "INTO symbols" in sql and params[2] == "Cart")
    total_row = symbol_rows[ids["Cart.total"]]
    assert total_row[4] == cart_insert + 1, "Cart.total points at the id returned for Cart"
    print("OK - parent_id is the id the class header got")
