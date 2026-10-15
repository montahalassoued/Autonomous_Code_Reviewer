import re
import sys
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).parent / "migrations"
MIGRATION_FILE_PATTERN = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")

# Any fixed number works; it only has to be the same for every process that migrates.
MIGRATION_LOCK_ID = 4_172_031

CREATE_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


# (version, sql) for every migration file, in order; the version is the file name without .sql.
def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, str]]:
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        if not MIGRATION_FILE_PATTERN.match(path.name):
            raise ValueError(f"Migration file {path.name} must be named like 001_short_description.sql")
        migrations.append((path.stem, path.read_text(encoding="utf-8")))
    return migrations


# Apply the missing migrations in one transaction; returns the versions applied now.
def apply_migrations(connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    applied_now = []
    with connection.transaction():
        # Two replicas starting together wait here instead of migrating twice.
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
        connection.execute(CREATE_HISTORY_TABLE_SQL)
        already_applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        for version, sql in list_migrations(directory):
            if version in already_applied:
                continue
            connection.execute(sql)
            connection.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied_now.append(version)
    return applied_now


def main() -> None:
    import psycopg

    from db.postgres import database_url

    with psycopg.connect(database_url()) as connection:
        applied_now = apply_migrations(connection)
    print(f"Applied: {', '.join(applied_now)}" if applied_now else "Schema is up to date.")


if __name__ == "__main__":
    sys.exit(main())
