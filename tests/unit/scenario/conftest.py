# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Isolation fixtures shared by scenario unit tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration


@pytest.fixture(autouse=True)
def block_dataset_provider_lookup() -> Generator[AsyncMock, None, None]:
    """Require inline seeds, mocked preparation, or an explicit fake-provider lookup."""
    with patch.object(
        SeedDatasetProvider,
        "get_providers_by_name_async",
        new_callable=AsyncMock,
        side_effect=AssertionError(
            "Scenario unit tests must not resolve live dataset providers. "
            "Use inline seeds, mock prepare_async, or patch lookup with fake providers in preparation tests."
        ),
    ) as lookup:
        yield lookup


@pytest.fixture
def mock_dataset_preparation() -> Generator[AsyncMock, None, None]:
    """Skip preparation only in tests that supply their own mocked seed populations."""
    with patch.object(DatasetConfiguration, "prepare_async", new_callable=AsyncMock) as prepare:
        yield prepare
