import pytest

from spott.core.db import init_registry_db
from spott.ingest.common.registry import Registry


@pytest.fixture
def registry(pg) -> Registry:
    """An empty registry in the test's own schema."""
    init_registry_db(pg)
    return Registry(pg)
