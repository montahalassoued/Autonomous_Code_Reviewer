from pathlib import Path

import pytest


INTEGRATION_DIR = Path(__file__).parent / "integration"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Adds the integration marker to every test that lives in tests/integration/."""
    for item in items:
        if INTEGRATION_DIR in Path(item.fspath).parents:
            item.add_marker(pytest.mark.integration)
