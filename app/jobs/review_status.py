import threading
from dataclasses import dataclass
from typing import Protocol

from app.jobs.models import DedupeKey


# Why a review was skipped without posting.
SKIP_SUPERSEDED = "superseded"  # the PR moved to a newer commit, whose own job reviews it
SKIP_CLOSED = "closed"  # the PR was closed or merged before the review was posted
SKIP_NO_CODE = "no reviewable code"  # a push whose diff has nothing the agents review: no comment is posted
MAX_REASON_CHARS = 500


# What one review spent on the LLM: model responses and tokens, all agents together.
@dataclass(frozen=True)
class ReviewCost:
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


# What the worker records about one review in the reviews table.
class ReviewStatusStore(Protocol):

    def mark_running(self, key: DedupeKey) -> None:
        ...

    def mark_posted(self, key: DedupeKey, latency_ms: int, missing_categories: list[str], cost: ReviewCost) -> None:
        ...

    # cost is set when the review ran before going stale: its tokens were spent anyway.
    def mark_skipped(self, key: DedupeKey, reason: str, cost: ReviewCost | None = None) -> None:
        ...

    def mark_failed(self, key: DedupeKey, reason: str) -> None:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class ReviewStatus:
    status: str
    reason: str | None = None
    latency_ms: int | None = None
    missing_categories: tuple[str, ...] = ()
    cost: ReviewCost | None = None


# For local development and tests: keeps the latest status per review in memory.
class InMemoryReviewStatusStore:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.statuses: dict[DedupeKey, ReviewStatus] = {}

    def _set(self, key: DedupeKey, status: ReviewStatus) -> None:
        with self._lock:
            if self.statuses.get(key, ReviewStatus("queued")).status != "posted" or status.status == "posted":
                self.statuses[key] = status

    def mark_running(self, key: DedupeKey) -> None:
        self._set(key, ReviewStatus("running"))

    def mark_posted(self, key: DedupeKey, latency_ms: int, missing_categories: list[str], cost: ReviewCost) -> None:
        self._set(key, ReviewStatus("posted", latency_ms=latency_ms, missing_categories=tuple(missing_categories), cost=cost))

    def mark_skipped(self, key: DedupeKey, reason: str, cost: ReviewCost | None = None) -> None:
        self._set(key, ReviewStatus("skipped", reason=reason[:MAX_REASON_CHARS], cost=cost))

    def mark_failed(self, key: DedupeKey, reason: str) -> None:
        self._set(key, ReviewStatus("failed", reason=reason[:MAX_REASON_CHARS]))

    def close(self) -> None:
        pass  # nothing to free


UPSERT_REPO_SQL = """
INSERT INTO repos (full_name) VALUES (%s)
ON CONFLICT (full_name) DO UPDATE SET full_name = EXCLUDED.full_name
RETURNING id
"""

# An upsert, so the status lands even if the API deduped in memory; a posted review is never moved back.
# A status without a cost (running, failed) keeps the cost already stored.
SET_STATUS_SQL = """
INSERT INTO reviews (repo_id, target, head_sha, status, status_reason, latency_ms, missing_categories,
                     llm_calls, tokens_in, tokens_out)
VALUES (%(repo_id)s, %(target)s, %(head_sha)s, %(status)s, %(reason)s, %(latency_ms)s, %(missing)s,
        COALESCE(%(llm_calls)s, 0), COALESCE(%(tokens_in)s, 0), COALESCE(%(tokens_out)s, 0))
ON CONFLICT (repo_id, target, head_sha) DO UPDATE
SET status = EXCLUDED.status,
    status_reason = EXCLUDED.status_reason,
    latency_ms = COALESCE(EXCLUDED.latency_ms, reviews.latency_ms),
    missing_categories = EXCLUDED.missing_categories,
    llm_calls = COALESCE(%(llm_calls)s, reviews.llm_calls),
    tokens_in = COALESCE(%(tokens_in)s, reviews.tokens_in),
    tokens_out = COALESCE(%(tokens_out)s, reviews.tokens_out)
WHERE reviews.status <> 'posted' OR EXCLUDED.status = 'posted'
"""


# The reviews table as the review lifecycle: queued (webhook) -> running -> posted | skipped | failed.
class PostgresReviewStatusStore:

    def __init__(self, pool) -> None:
        self._pool = pool

    def _set(self, key: DedupeKey, status: str, reason: str | None = None, latency_ms: int | None = None,
             missing_categories: list[str] | None = None, cost: ReviewCost | None = None) -> None:
        with self._pool.connection() as connection:
            repo_id = connection.execute(UPSERT_REPO_SQL, (key.repo_full_name,)).fetchone()[0]
            connection.execute(SET_STATUS_SQL, {
                "repo_id": repo_id, "target": key.target, "head_sha": key.head_sha, "status": status,
                "reason": reason[:MAX_REASON_CHARS] if reason else None, "latency_ms": latency_ms,
                "missing": list(missing_categories or []),
                "llm_calls": cost.llm_calls if cost else None,
                "tokens_in": cost.tokens_in if cost else None,
                "tokens_out": cost.tokens_out if cost else None,
            })

    def mark_running(self, key: DedupeKey) -> None:
        self._set(key, "running")

    def mark_posted(self, key: DedupeKey, latency_ms: int, missing_categories: list[str], cost: ReviewCost) -> None:
        self._set(key, "posted", latency_ms=latency_ms, missing_categories=missing_categories, cost=cost)

    def mark_skipped(self, key: DedupeKey, reason: str, cost: ReviewCost | None = None) -> None:
        self._set(key, "skipped", reason=reason, cost=cost)

    def mark_failed(self, key: DedupeKey, reason: str) -> None:
        self._set(key, "failed", reason=reason)

    def close(self) -> None:
        self._pool.close()
