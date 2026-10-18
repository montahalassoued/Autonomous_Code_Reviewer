import os

from app.jobs.dedupe import DedupeStore, InMemoryDedupeStore, PostgresDedupeStore
from app.jobs.queue import AzureStorageJobQueue, InMemoryJobQueue, JobQueue
from app.jobs.review_status import InMemoryReviewStatusStore, PostgresReviewStatusStore, ReviewStatusStore


DEFAULT_QUEUE_NAME = "review-jobs"


# Reads JOBS_BACKEND and validates it.
def _backend() -> str:
    backend = os.environ.get("JOBS_BACKEND", "memory").strip().lower()
    if backend not in {"memory", "azure"}:
        raise ValueError(f"JOBS_BACKEND must be 'memory' or 'azure', got '{backend}'")
    return backend


# Returns AZURE_STORAGE_ACCOUNT_NAME or fails with a clear message.
def _account_name() -> str:
    name = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME")
    if not name:
        raise RuntimeError(
            "JOBS_BACKEND=azure needs AZURE_STORAGE_CONNECTION_STRING (local/Azurite) "
            "or AZURE_STORAGE_ACCOUNT_NAME (managed identity in Azure)."
        )
    return name


# Builds a QueueClient and creates the queue if it does not exist.
def _make_queue_client(queue_name: str):
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.queue import QueueClient

    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if connection_string:
        client = QueueClient.from_connection_string(connection_string, queue_name)
    else:
        from azure.identity import DefaultAzureCredential

        client = QueueClient(
            account_url=f"https://{_account_name()}.queue.core.windows.net",
            queue_name=queue_name,
            credential=DefaultAzureCredential(),
        )
    try:
        client.create_queue()
    except ResourceExistsError:
        pass  # normal after the first start
    return client


# Returns the job queue for the configured backend.
def build_queue() -> JobQueue:
    if _backend() == "memory":
        return InMemoryJobQueue()
    queue_name = os.environ.get("REVIEW_QUEUE_NAME", DEFAULT_QUEUE_NAME)
    return AzureStorageJobQueue(
        _make_queue_client(queue_name),
        _make_queue_client(f"{queue_name}-poison"),
    )


# DEDUPE_BACKEND: memory or postgres; defaults to postgres when the queue is Azure, so replicas share it.
def _dedupe_backend() -> str:
    default = "postgres" if _backend() == "azure" else "memory"
    backend = os.environ.get("DEDUPE_BACKEND", default).strip().lower()
    if backend not in {"memory", "postgres"}:
        raise ValueError(f"DEDUPE_BACKEND must be 'memory' or 'postgres', got '{backend}'")
    return backend


# A Postgres pool on an up-to-date schema.
def _migrated_pool():
    from db.migration_runner import apply_migrations
    from db.postgres import build_pool

    pool = build_pool()
    with pool.connection() as connection:
        apply_migrations(connection)
    return pool


# Returns the dedupe store; the postgres one first brings the schema up to date.
def build_dedupe() -> DedupeStore:
    if _dedupe_backend() == "memory":
        return InMemoryDedupeStore()
    return PostgresDedupeStore(_migrated_pool())


# The worker's review status store: same backend as the dedupe, since both live in the reviews table.
def build_review_status() -> ReviewStatusStore:
    if _dedupe_backend() == "memory":
        return InMemoryReviewStatusStore()
    return PostgresReviewStatusStore(_migrated_pool())
