import os
from pathlib import Path

import pytest

# Forced, not setdefault: the real .env may hold Langfuse keys, and a test must never send a trace.
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"


INTEGRATION_DIR = Path(__file__).parent / "integration"


# Adds the integration marker to every test that lives in tests/integration/.
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if INTEGRATION_DIR in Path(item.fspath).parents:
            item.add_marker(pytest.mark.integration)
