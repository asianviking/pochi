import sys
from pathlib import Path

import pytest
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_structlog():
    """Reset structlog configuration before each test to avoid caching issues."""
    # Reset structlog to allow reconfiguration
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()
