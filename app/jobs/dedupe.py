import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

from app.jobs.models import DedupeKey


class DedupeStore(Protocol):
    """What the webhook needs from a dedupe store."""

    def claim(self, key: DedupeKey) -> bool:
        """Atomically records the key. True if this call claimed it, False if already claimed."""

    def release(self, key: DedupeKey) -> None:
        """Forgets the key so a redelivery can claim it again."""


class InMemoryDedupeStore:
    """
    Thread-safe dedupe for local development and tests (single process only).

    Args:
        ttl_s: if set, a claim expires after this many seconds.
        clock: returns "now" in seconds; injectable for tests.
    """

    def __init__(self, ttl_s: Optional[float] = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._claimed_at: dict[DedupeKey, float] = {}

    def claim(self, key: DedupeKey) -> bool:
        """Claims the key unless an unexpired claim exists."""
        with self._lock:
            now = self._clock()
            claimed_at = self._claimed_at.get(key)
            if claimed_at is not None and (self._ttl_s is None or now - claimed_at < self._ttl_s):
                return False
            self._claimed_at[key] = now
            return True

    def release(self, key: DedupeKey) -> None:
        """Removes the claim if present."""
        with self._lock:
            self._claimed_at.pop(key, None)


_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9.\-]")


def encode_table_key(value: str) -> str:
    """
    Makes a string safe for an Azure Table PartitionKey/RowKey.

    Table keys may not contain / \\ # ? or control characters. Every character
    outside [A-Za-z0-9.-] becomes "_" + two hex digits. "_" itself is escaped
    too, so the mapping is reversible and two different inputs never collide.

    Args:
        value: raw text such as "owner/repo".

    Returns:
        the encoded text.
    """
    return _UNSAFE_KEY_CHARS.sub(lambda match: f"_{ord(match.group()):02x}", value)


def table_keys(key: DedupeKey) -> tuple[str, str]:
    """
    Maps a DedupeKey to (PartitionKey, RowKey).

    Partition = repo (so one repo's rows sit together). Row = sha + target,
    joined with "~", which the encoder never produces, so parts cannot blur.
    """
    partition_key = encode_table_key(key.repo_full_name)
    row_key = f"{encode_table_key(key.head_sha)}~{encode_table_key(key.target)}"
    return partition_key, row_key


class AzureTableDedupeStore:
    """
    Azure Table Storage dedupe store.

    Args:
        table_client: an azure.data.tables TableClient for an existing table
                      (the factory creates the table if missing).
    """

    def __init__(self, table_client) -> None:
        self._table = table_client

    def claim(self, key: DedupeKey) -> bool:
        """Inserts the row. A conflict means somebody already claimed it."""
        from azure.core.exceptions import ResourceExistsError  # lazy: memory mode needs no Azure

        partition_key, row_key = table_keys(key)
        entity = {
            "PartitionKey": partition_key,
            "RowKey": row_key,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._table.create_entity(entity)
        except ResourceExistsError:
            return False
        return True

    def release(self, key: DedupeKey) -> None:
        """Deletes the row; a missing row is fine."""
        from azure.core.exceptions import ResourceNotFoundError

        partition_key, row_key = table_keys(key)
        try:
            self._table.delete_entity(partition_key, row_key)
        except ResourceNotFoundError:
            pass  # already released, nothing to undo
