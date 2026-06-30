# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend dataset service.
"""

from unittest.mock import AsyncMock, patch

from pyrit.backend.services.dataset_service import DatasetService, get_dataset_service


class TestListDatasets:
    """Tests for DatasetService.list_datasets_async."""

    async def test_list_datasets(self):
        service = DatasetService()

        with patch(
            "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
            new_callable=AsyncMock,
            return_value=["airt_hate", "harmbench"],
        ):
            result = await service.list_datasets_async()

        assert [item.name for item in result.items] == ["airt_hate", "harmbench"]

    async def test_list_datasets_empty(self):
        service = DatasetService()

        with patch(
            "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await service.list_datasets_async()

        assert result.items == []


def test_get_dataset_service_is_singleton():
    assert get_dataset_service() is get_dataset_service()
