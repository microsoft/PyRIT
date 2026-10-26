# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Isolation fixtures shared by scenario unit tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.scenario.core.dataset_configuration import DatasetConfiguration


@pytest.fixture(autouse=True)
def enforce_dataset_isolation(block_dataset_provider_lookup: AsyncMock) -> None:
    """Apply the shared provider guard to every scenario unit test."""


@pytest.fixture
def mock_dataset_preparation() -> Generator[AsyncMock, None, None]:
    """Skip preparation only in tests that supply their own mocked seed populations."""
    with patch.object(DatasetConfiguration, "prepare_async", new_callable=AsyncMock) as prepare:
        yield prepare
