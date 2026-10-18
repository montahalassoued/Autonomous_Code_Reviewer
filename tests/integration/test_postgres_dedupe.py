import os
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv()

from app.jobs.dedupe import PostgresDedupeStore  # noqa: E402 - .env must be loaded before DATABASE_URL is read
from app.jobs.models import DedupeKey  # noqa: E402
from app.jobs.review_status import PostgresReviewStatusStore  # noqa: E402
from db.migration_runner import apply_migrations  # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL (docker compose up postgres)")


@pytest.fixture
def pool():
    from db.postgres import build_pool

    pool = build_pool()
    with pool.connection() as connection:
        apply_migrations(connection)
    yield pool
    pool.close()


# A repo name no other run uses, so the test never sees old rows.
def fresh_key(target: str = "pr:1") -> DedupeKey:
    return DedupeKey(f"test/{uuid.uuid4().hex[:12]}", "a" * 40, target)


def test_migrations_are_idempotent(pool):
    with pool.connection() as connection:
        assert apply_migrations(connection) == [], "a second run must apply nothing"
    print("OK - migrations applied once, second run is a no-op")


def test_claim_duplicate_release_on_real_postgres(pool):
    store = PostgresDedupeStore(pool)
    key = fresh_key()

    assert store.claim(key) is True
    assert store.claim(key) is False, "the unique constraint must reject the same commit twice"
    store.release(key)
    assert store.claim(key) is True, "a released claim can be taken again"
    print("OK - claim, duplicate, release, claim again against Postgres")


def test_release_keeps_a_running_review(pool):
    store = PostgresDedupeStore(pool)
    key = fresh_key()
    store.claim(key)
    with pool.connection() as connection:
        connection.execute(
            "UPDATE reviews SET status = 'running' FROM repos "
            "WHERE reviews.repo_id = repos.id AND repos.full_name = %s",
            (key.repo_full_name,),
        )

    store.release(key)

    assert store.claim(key) is False, "release must not delete a review that already started"
    print("OK - only queued reviews are released")


def status_of(pool, key: DedupeKey) -> tuple:
    with pool.connection() as connection:
        return connection.execute(
            "SELECT status, status_reason, missing_categories FROM reviews JOIN repos ON repos.id = reviews.repo_id "
            "WHERE repos.full_name = %s AND target = %s AND head_sha = %s",
            (key.repo_full_name, key.target, key.head_sha),
        ).fetchone()


def test_review_lifecycle_and_retry_of_a_failed_review(pool):
    dedupe, status = PostgresDedupeStore(pool), PostgresReviewStatusStore(pool)
    key = fresh_key()

    assert dedupe.claim(key) is True
    status.mark_running(key)
    status.mark_failed(key, "RuntimeError: Groq 503")
    assert status_of(pool, key) == ("failed", "RuntimeError: Groq 503", [])

    assert dedupe.claim(key) is True, "a redelivery reopens a failed review"
    assert status_of(pool, key)[0] == "queued"
    status.mark_running(key)
    status.mark_posted(key, 1234, ["logic"])
    assert status_of(pool, key) == ("posted", None, ["logic"])

    status.mark_running(key)
    assert status_of(pool, key)[0] == "posted", "a replayed job never moves a posted review back"
    assert dedupe.claim(key) is False, "a posted review stays a duplicate"
    print("OK - queued -> running -> failed -> queued -> posted, against the real schema")
