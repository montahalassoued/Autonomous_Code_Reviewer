from contextlib import contextmanager

import pytest

from db.migration_runner import MIGRATION_LOCK_ID, MIGRATIONS_DIR, apply_migrations, list_migrations


# Records every statement; SELECT version returns the versions marked as already applied.
class FakeConnection:

    def __init__(self, already_applied: list[str] | None = None) -> None:
        self.already_applied = already_applied or []
        self.statements: list[tuple[str, tuple | None]] = []
        self.in_transaction = False

    @contextmanager
    def transaction(self):
        self.in_transaction = True
        yield
        self.in_transaction = False

    def execute(self, sql: str, params: tuple | None = None):
        assert self.in_transaction, "every migration statement must run inside the transaction"
        self.statements.append((sql.strip(), params))
        if sql.strip().startswith("SELECT version"):
            return [(version,) for version in self.already_applied]
        return []


def write_migrations(directory, names: list[str]) -> None:
    for name in names:
        (directory / name).write_text(f"-- {name}\nCREATE TABLE t_{name[:3]} (id INT);", encoding="utf-8")


def test_applies_missing_migrations_in_order_after_taking_the_lock(tmp_path):
    write_migrations(tmp_path, ["002_second.sql", "001_first.sql", "003_third.sql"])
    connection = FakeConnection(already_applied=["001_first"])

    applied = apply_migrations(connection, tmp_path)

    assert applied == ["002_second", "003_third"]
    assert connection.statements[0] == ("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,)), "lock first"
    recorded = [params[0] for sql, params in connection.statements if sql.startswith("INSERT INTO schema_migrations")]
    assert recorded == ["002_second", "003_third"]
    print("OK - only missing migrations run, in file order, under the advisory lock")


def test_up_to_date_schema_applies_nothing(tmp_path):
    write_migrations(tmp_path, ["001_first.sql"])

    assert apply_migrations(FakeConnection(already_applied=["001_first"]), tmp_path) == []
    print("OK - a second start is a no-op")


def test_rejects_badly_named_migration_files(tmp_path):
    write_migrations(tmp_path, ["1_first.sql"])

    with pytest.raises(ValueError, match="001_short_description.sql"):
        list_migrations(tmp_path)
    print("OK - migration files must be named NNN_description.sql")


def test_initial_schema_creates_every_table():
    versions = [version for version, _sql in list_migrations(MIGRATIONS_DIR)]
    schema = dict(list_migrations(MIGRATIONS_DIR))["001_initial_schema"]

    assert versions[0] == "001_initial_schema"
    for table in ("repos", "files", "symbols", "edges", "unresolved_calls", "reviews", "findings"):
        assert f"CREATE TABLE {table} (" in schema, f"001_initial_schema.sql must create {table}"
    assert "UNIQUE (repo_id, target, head_sha)" in schema, "the reviews unique key is the dedupe key"
    print("OK - the initial migration creates the 7 tables and the dedupe key")


if __name__ == "__main__":
    test_initial_schema_creates_every_table()
    print("Run under pytest for the tmp_path tests.")
