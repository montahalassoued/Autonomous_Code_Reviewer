import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

from app.jobs.models import DedupeKey


# What the webhook needs from a dedupe store.
class DedupeStore(Protocol):

    # Atomically records the key.
    def claim(self, key: DedupeKey) -> bool:
        ...

    # Forgets the key so a redelivery can claim it again.
    def release(self, key: DedupeKey) -> None:
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

    # Removes the claim if present.
    def release(self, key: DedupeKey) -> None:
        with self._lock:
            self._claimed_at.pop(key, None)


_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9.\-]")


# Makes a string safe for an Azure Table PartitionKey/RowKey.
def encode_table_key(value: str) -> str:
    return _UNSAFE_KEY_CHARS.sub(lambda match: f"_{ord(match.group()):02x}", value)


# Maps a DedupeKey to (PartitionKey, RowKey).
def table_keys(key: DedupeKey) -> tuple[str, str]:
    partition_key = encode_table_key(key.repo_full_name)
    row_key = f"{encode_table_key(key.head_sha)}~{encode_table_key(key.target)}"
    return partition_key, row_key


# Azure Table Storage dedupe store.
class AzureTableDedupeStore:

    def __init__(self, table_client) -> None:
        self._table = table_client

    # Inserts the row.
    def claim(self, key: DedupeKey) -> bool:
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

    # Deletes the row; a missing row is fine.
    def release(self, key: DedupeKey) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        partition_key, row_key = table_keys(key)
        try:
            self._table.delete_entity(partition_key, row_key)
        except ResourceNotFoundError:
            pass  # already released, nothing to undo
