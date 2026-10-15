import pytest

from app.jobs.dedupe import InMemoryDedupeStore, PostgresDedupeStore
from app.jobs.models import DedupeKey


KEY = DedupeKey("owner/repo", "abc123", "pr:42")


def test_claim_once_then_duplicate_then_release():
    store = InMemoryDedupeStore()

    assert store.claim(KEY) is True
    assert store.claim(KEY) is False, "Second claim of the same key must fail"
    store.release(KEY)
    assert store.claim(KEY) is True, "A released key can be claimed again"
    print("OK - claim, duplicate, release, claim again")


def test_different_sha_is_a_different_key():
    store = InMemoryDedupeStore()

    assert store.claim(KEY) is True
    assert store.claim(DedupeKey("owner/repo", "def456", "pr:42")) is True, "A new commit must be reviewed"
    print("OK - a new head sha is not a duplicate")


def test_in_memory_claim_expires_after_ttl():
    now = [0.0]
    store = InMemoryDedupeStore(ttl_s=10, clock=lambda: now[0])

    assert store.claim(KEY) is True
    now[0] = 5
    assert store.claim(KEY) is False
    now[0] = 11
    assert store.claim(KEY) is True, "An expired claim can be taken again"
    print("OK - claims expire after the TTL")


# Mimics the reviews/repos tables: one row per (repo, target, sha), like the real unique constraint.
class FakeConnection:

    def __init__(self, tables: dict) -> None:
        self.tables = tables
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        pass

    def execute(self, sql: str, params: tuple):
        repos, reviews = self.tables["repos"], self.tables["reviews"]
        if sql.strip().startswith("INSERT INTO repos"):
            self._result = (repos.setdefault(params[0], len(repos) + 1),)
        elif sql.strip().startswith("INSERT INTO reviews"):
            key = params
            self._result = None if key in reviews else (len(reviews) + 1,)
            reviews.setdefault(key, "queued")
        elif sql.strip().startswith("DELETE FROM reviews"):
            full_name, target, head_sha = params
            key = (repos.get(full_name), target, head_sha)
            if reviews.get(key) == "queued":
                del reviews[key]
        return self

    def fetchone(self):
        return self._result


class FakePool:

    def __init__(self) -> None:
        self.tables = {"repos": {}, "reviews": {}}
        self.closed = False

    def connection(self) -> FakeConnection:
        return FakeConnection(self.tables)

    def close(self) -> None:
        self.closed = True


def test_postgres_store_claims_once_per_commit_and_target():
    store = PostgresDedupeStore(FakePool())

    assert store.claim(KEY) is True
    assert store.claim(KEY) is False, "The unique (repo, target, sha) row means already queued"
    assert store.claim(DedupeKey("owner/repo", "abc123", "push:main")) is True, "Another target is another review"
    print("OK - one reviews row per commit and target")


def test_postgres_store_release_only_forgets_queued_reviews():
    pool = FakePool()
    store = PostgresDedupeStore(pool)
    store.claim(KEY)
    store.release(KEY)
    assert store.claim(KEY) is True, "A released claim can be taken again"

    pool.tables["reviews"][(1, KEY.target, KEY.head_sha)] = "running"
    store.release(KEY)
    assert store.claim(KEY) is False, "A running review must never be forgotten"
    print("OK - release deletes a queued row only")


def test_postgres_store_close_closes_the_pool():
    pool = FakePool()
    PostgresDedupeStore(pool).close()

    assert pool.closed
    print("OK - close() releases the connection pool")


def test_dedupe_backend_defaults(monkeypatch):
    from app.jobs import factory

    monkeypatch.delenv("DEDUPE_BACKEND", raising=False)
    monkeypatch.setenv("JOBS_BACKEND", "memory")
    assert factory._dedupe_backend() == "memory"
    monkeypatch.setenv("JOBS_BACKEND", "azure")
    assert factory._dedupe_backend() == "postgres", "Azure replicas must share one dedupe store"
    monkeypatch.setenv("DEDUPE_BACKEND", "redis")
    with pytest.raises(ValueError):
        factory._dedupe_backend()
    print("OK - memory locally, postgres with the Azure queue, anything else rejected")


if __name__ == "__main__":
    test_claim_once_then_duplicate_then_release()
    test_different_sha_is_a_different_key()
    test_in_memory_claim_expires_after_ttl()
    test_postgres_store_claims_once_per_commit_and_target()
    test_postgres_store_release_only_forgets_queued_reviews()
    test_postgres_store_close_closes_the_pool()
    print("\nDedupe tests passed (the backend test runs under pytest).")
