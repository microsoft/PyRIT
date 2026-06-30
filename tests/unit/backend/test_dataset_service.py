# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend dataset service.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.models.datasets import LoadDatasetRequest
from pyrit.backend.services.dataset_service import DatasetService, get_dataset_service
from pyrit.models.seeds import SeedDataset


def _seed_dataset(*, name: str, count: int) -> SeedDataset:
    """Build a small SeedDataset with ``count`` prompt seeds."""
    seeds = [{"value": f"{name}-{i}", "data_type": "text"} for i in range(count)]
    return SeedDataset(dataset_name=name, seeds=seeds)


class TestListDatasets:
    """Tests for DatasetService.list_datasets_async."""

    async def test_list_datasets_marks_loaded(self):
        service = DatasetService()

        memory = MagicMock()
        memory.get_seed_dataset_names.return_value = ["airt_hate"]

        with (
            patch(
                "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
                new_callable=AsyncMock,
                return_value=["airt_hate", "harmbench"],
            ),
            patch(
                "pyrit.backend.services.dataset_service.CentralMemory.get_memory_instance",
                return_value=memory,
            ),
        ):
            result = await service.list_datasets_async()

        by_name = {item.name: item.loaded for item in result.items}
        assert by_name == {"airt_hate": True, "harmbench": False}

    async def test_list_datasets_empty(self):
        service = DatasetService()
        memory = MagicMock()
        memory.get_seed_dataset_names.return_value = []

        with (
            patch(
                "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "pyrit.backend.services.dataset_service.CentralMemory.get_memory_instance",
                return_value=memory,
            ),
        ):
            result = await service.list_datasets_async()

        assert result.items == []


class TestLoadDatasets:
    """Tests for DatasetService.load_datasets_async."""

    async def test_load_datasets_adds_to_memory(self):
        service = DatasetService()
        datasets = [_seed_dataset(name="airt_hate", count=3), _seed_dataset(name="harmbench", count=2)]

        memory = MagicMock()
        memory.add_seed_datasets_to_memory_async = AsyncMock()

        with (
            patch(
                "pyrit.backend.services.dataset_service.SeedDatasetProvider.fetch_datasets_async",
                new_callable=AsyncMock,
                return_value=datasets,
            ),
            patch(
                "pyrit.backend.services.dataset_service.CentralMemory.get_memory_instance",
                return_value=memory,
            ),
        ):
            result = await service.load_datasets_async(
                request=LoadDatasetRequest(dataset_names=["airt_hate", "harmbench"])
            )

        memory.add_seed_datasets_to_memory_async.assert_awaited_once()
        assert result.total_seeds == 5
        assert {d.name: d.seed_count for d in result.loaded_datasets} == {"airt_hate": 3, "harmbench": 2}

    async def test_load_datasets_propagates_value_error(self):
        service = DatasetService()

        with patch(
            "pyrit.backend.services.dataset_service.SeedDatasetProvider.fetch_datasets_async",
            new_callable=AsyncMock,
            side_effect=ValueError("Dataset(s) not found"),
        ):
            with pytest.raises(ValueError, match="not found"):
                await service.load_datasets_async(request=LoadDatasetRequest(dataset_names=["nope"]))


def test_get_dataset_service_is_singleton():
    assert get_dataset_service() is get_dataset_service()
