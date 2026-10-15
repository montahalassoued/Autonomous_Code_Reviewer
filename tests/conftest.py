"""
conftest.py - shared pytest setup for the whole test suite.

Inputs: every collected test.
Outputs: the "integration" marker on every test under tests/integration/.

Why: integration tests make real Groq/Gemini/Voyage/GitHub calls and use
API quota. Marking them by folder (instead of by hand in each file) means a
new integration test can never be run by accident in CI: CI runs
`pytest tests/unit`, and `pytest -m "not integration"` also excludes them
when the whole suite is run.
"""

from pathlib import Path

import pytest


INTEGRATION_DIR = Path(__file__).parent / "integration"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Adds the integration marker to every test that lives in tests/integration/."""
    for item in items:
        if INTEGRATION_DIR in Path(item.fspath).parents:
            item.add_marker(pytest.mark.integration)
