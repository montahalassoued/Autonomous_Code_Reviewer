import os

from app.jobs.dedupe import AzureTableDedupeStore, DedupeStore, InMemoryDedupeStore
from app.jobs.queue import AzureStorageJobQueue, InMemoryJobQueue, JobQueue


DEFAULT_QUEUE_NAME = "review-jobs"
DEFAULT_DEDUPE_TABLE = "reviewdedupe"


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


# Returns the dedupe store for the configured backend.
def build_dedupe() -> DedupeStore:
    if _backend() == "memory":
        return InMemoryDedupeStore()

    from azure.core.exceptions import ResourceExistsError
    from azure.data.tables import TableClient

    table_name = os.environ.get("REVIEW_DEDUPE_TABLE", DEFAULT_DEDUPE_TABLE)
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if connection_string:
        table = TableClient.from_connection_string(connection_string, table_name=table_name)
    else:
        from azure.identity import DefaultAzureCredential

        table = TableClient(
            endpoint=f"https://{_account_name()}.table.core.windows.net",
            table_name=table_name,
            credential=DefaultAzureCredential(),
        )
    try:
        table.create_table()
    except ResourceExistsError:
        pass  # normal after the first start
    return AzureTableDedupeStore(table)
