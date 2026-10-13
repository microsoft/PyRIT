# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared test isolation without importing another suite's conftest."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

    from pyrit.memory import SQLiteMemory


@contextmanager
def use_sqlite_memory(sqlite_memory: SQLiteMemory) -> Generator[None, None, None]:
    """Temporarily bind CentralMemory and the SQLite singleton to a fixture-owned instance."""
    from pyrit.common.singleton import Singleton
    from pyrit.memory import CentralMemory, SQLiteMemory

    previous_instance = Singleton._instances.get(SQLiteMemory)
    Singleton._instances[SQLiteMemory] = sqlite_memory
    try:
        with patch.object(CentralMemory, "_memory_instance", None):
            CentralMemory.set_memory_instance(sqlite_memory)
            yield
    finally:
        if previous_instance is None:
            Singleton._instances.pop(SQLiteMemory, None)
        else:
            Singleton._instances[SQLiteMemory] = previous_instance


@pytest.fixture(autouse=True)
def compatibility_id() -> Iterator[str]:
    """Supply startup provenance and initialize clients that deliberately skip lifespan."""
    from pyrit import _compatibility
    from pyrit.backend.main import app

    identity = "0.14.0+g" + "a" * 40
    with (
        patch.object(_compatibility, "get_compatibility_id", return_value=identity),
        patch.object(app.state, "compatibility_id", identity, create=True),
    ):
        yield identity


@pytest.fixture
def compatibility_headers(compatibility_id: str) -> dict[str, str]:
    """Provide the marker required by business API requests."""
    from pyrit import _compatibility

    return {_compatibility.COMPATIBILITY_HEADER: compatibility_id}
