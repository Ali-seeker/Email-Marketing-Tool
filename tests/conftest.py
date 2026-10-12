"""Shared pytest fixtures."""

import pytest

from app.core.clock import FakeClock
from app.core.config import Settings


@pytest.fixture
def fake_clock() -> FakeClock:
    """Provide an injectable FakeClock starting at a fixed UTC time."""
    return FakeClock()


@pytest.fixture
def default_settings() -> Settings:
    """Provide freshly initialized default settings."""
    return Settings(_env_file=None)
