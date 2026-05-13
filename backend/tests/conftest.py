"""Pytest config — wires asyncio mode for any async tests."""

from __future__ import annotations

import pytest

pytest_plugins = ["pytest_asyncio"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
