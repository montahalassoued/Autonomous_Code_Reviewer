"""
Unit test for app/jobs/dedupe.py

No Azure and no network: the in-memory store is tested directly, and the
Azure Table store is tested with a fake TableClient that raises the real
azure.core ResourceExistsError / ResourceNotFoundError (azure-core is
installed with azure-data-tables; the tests skip if it is missing).
"""

import pytest

from app.jobs.dedupe import AzureTableDedupeStore, InMemoryDedupeStore, encode_table_key, table_keys
from app.jobs.models import DedupeKey


KEY = DedupeKey("owner/repo", "abc123", "pr:42")
FORBIDDEN_TABLE_KEY_CHARS = set("/\\#?")


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


def test_table_keys_have_no_forbidden_characters():
    partition_key, row_key = table_keys(DedupeKey("owner/repo#1?", "abc\\123", "pr:42"))

    for part in (partition_key, row_key):
        assert not (set(part) & FORBIDDEN_TABLE_KEY_CHARS), f"Forbidden character in {part}"
    assert partition_key.startswith("owner_2frepo")
    print(f"OK - table keys are valid: {partition_key} / {row_key}")


def test_table_key_encoding_does_not_collide():
    assert encode_table_key("a/b") != encode_table_key("a_2fb"), "Escaping must be injective"
    print("OK - key encoding is collision free")


class FakeTableClient:
    """Mimics the TableClient methods the store uses, with real Azure exceptions."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}

    def create_entity(self, entity: dict) -> None:
        from azure.core.exceptions import ResourceExistsError

        identity = (entity["PartitionKey"], entity["RowKey"])
        if identity in self.rows:
            raise ResourceExistsError("entity exists")
        self.rows[identity] = entity

    def delete_entity(self, partition_key: str, row_key: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        if (partition_key, row_key) not in self.rows:
            raise ResourceNotFoundError("no such entity")
        del self.rows[(partition_key, row_key)]


def test_azure_table_store_maps_conflict_to_false():
    pytest.importorskip("azure.core.exceptions")
    table = FakeTableClient()
    store = AzureTableDedupeStore(table)

    assert store.claim(KEY) is True
    assert store.claim(KEY) is False, "ResourceExistsError must become False"
    assert len(table.rows) == 1
    print("OK - Azure Table conflict means already claimed")


def test_azure_table_store_release_is_idempotent():
    pytest.importorskip("azure.core.exceptions")
    store = AzureTableDedupeStore(FakeTableClient())
    store.claim(KEY)

    store.release(KEY)
    store.release(KEY)  # must not raise on a missing row

    assert store.claim(KEY) is True
    print("OK - release ignores a missing row and allows a new claim")


if __name__ == "__main__":
    test_claim_once_then_duplicate_then_release()
    test_different_sha_is_a_different_key()
    test_in_memory_claim_expires_after_ttl()
    test_table_keys_have_no_forbidden_characters()
    test_table_key_encoding_does_not_collide()
    print("\nDedupe tests passed (Azure fake tests run under pytest).")
