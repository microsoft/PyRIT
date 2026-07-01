# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend dataset service.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.services.dataset_service import DatasetService, get_dataset_service


@pytest.fixture
def mock_memory():
    """Create a mock memory instance."""
    memory = MagicMock()
    memory.get_seed_dataset_names.return_value = []
    return memory


@pytest.fixture
def dataset_service(mock_memory):
    """Create a dataset service with mocked memory."""
    with patch("pyrit.backend.services.dataset_service.CentralMemory") as mock_central:
        mock_central.get_memory_instance.return_value = mock_memory
        yield DatasetService()


@pytest.mark.usefixtures("patch_central_database")
class TestListDatasets:
    """Tests for DatasetService.list_datasets_async."""

    async def test_list_datasets(self, dataset_service):
        with patch(
            "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
            new_callable=AsyncMock,
            return_value=["airt_hate", "harmbench"],
        ):
            result = await dataset_service.list_datasets_async()

        assert [item.name for item in result.items] == ["airt_hate", "harmbench"]

    async def test_list_datasets_empty(self, dataset_service):
        with patch(
            "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await dataset_service.list_datasets_async()

        assert result.items == []


@pytest.mark.usefixtures("patch_central_database")
def test_get_dataset_service_is_singleton():
    get_dataset_service.cache_clear()
    with patch("pyrit.backend.services.dataset_service.CentralMemory"):
        assert get_dataset_service() is get_dataset_service()
