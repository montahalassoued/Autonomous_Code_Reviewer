import threading
import time
from typing import Callable, Optional, Protocol

from app.jobs.models import DedupeKey


# What the webhook needs from a dedupe store.
class DedupeStore(Protocol):

    # Atomically records the key; False if it was already recorded.
    def claim(self, key: DedupeKey) -> bool:
        ...

    # Forgets the key so a redelivery can claim it again.
    def release(self, key: DedupeKey) -> None:
        ...

    # Frees connections on shutdown.
    def close(self) -> None:
        ...


# Thread-safe dedupe for local development and tests (single process only).
class InMemoryDedupeStore:

    def __init__(self, ttl_s: Optional[float] = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._claimed_at: dict[DedupeKey, float] = {}

    # Claims the key unless an unexpired claim exists.
    def claim(self, key: DedupeKey) -> bool:
        with self._lock:
            now = self._clock()
            claimed_at = self._claimed_at.get(key)
            if claimed_at is not None and (self._ttl_s is None or now - claimed_at < self._ttl_s):
                return False
            self._claimed_at[key] = now
            return True

    def release(self, key: DedupeKey) -> None:
        with self._lock:
            self._claimed_at.pop(key, None)

    def close(self) -> None:
        pass  # nothing to free


# DO UPDATE (a no-op) instead of DO NOTHING, so RETURNING gives the id of an existing repo too.
UPSERT_REPO_SQL = """
INSERT INTO repos (full_name) VALUES (%s)
ON CONFLICT (full_name) DO UPDATE SET full_name = EXCLUDED.full_name
RETURNING id
"""

# A failed review is reopened, so a manual redelivery retries it; any other existing row is a duplicate.
INSERT_REVIEW_SQL = """
INSERT INTO reviews (repo_id, target, head_sha) VALUES (%s, %s, %s)
ON CONFLICT (repo_id, target, head_sha) DO UPDATE
SET status = 'queued', status_reason = NULL
WHERE reviews.status = 'failed'
RETURNING id
"""

# Only a still-queued review is forgotten: a running or finished one keeps its row.
DELETE_QUEUED_REVIEW_SQL = """
DELETE FROM reviews USING repos
WHERE reviews.repo_id = repos.id
  AND repos.full_name = %s
  AND reviews.target = %s
  AND reviews.head_sha = %s
  AND reviews.status = 'queued'
"""


# Dedupe through the reviews table: its unique (repo_id, target, head_sha) is the claim.
class PostgresDedupeStore:

    def __init__(self, pool) -> None:
        self._pool = pool

    # Inserts a 'queued' review row (or reopens a failed one); False if this commit already has a review.
    def claim(self, key: DedupeKey) -> bool:
        with self._pool.connection() as connection:
            repo_id = connection.execute(UPSERT_REPO_SQL, (key.repo_full_name,)).fetchone()[0]
            inserted = connection.execute(INSERT_REVIEW_SQL, (repo_id, key.target, key.head_sha)).fetchone()
        return inserted is not None

    def release(self, key: DedupeKey) -> None:
        with self._pool.connection() as connection:
            connection.execute(DELETE_QUEUED_REVIEW_SQL, (key.repo_full_name, key.target, key.head_sha))

    def close(self) -> None:
        self._pool.close()
