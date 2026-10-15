import os


DEFAULT_POOL_MAX_SIZE = 5
POOL_OPEN_TIMEOUT_S = 10


# DATABASE_URL, or a clear error saying what to set.
def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Locally it is "
            "postgresql://reviewer:reviewer@localhost:5432/reviewer (see docker-compose.yml)."
        )
    return url


# Shared connection pool; each connection is checked before it is handed out.
def build_pool(url: str | None = None, max_size: int = DEFAULT_POOL_MAX_SIZE):
    from psycopg_pool import ConnectionPool  # lazy: memory mode and offline tests need no driver

    pool = ConnectionPool(
        url or database_url(),
        min_size=1,
        max_size=max_size,
        open=False,
        check=ConnectionPool.check_connection,
    )
    pool.open(wait=True, timeout=POOL_OPEN_TIMEOUT_S)
    return pool
