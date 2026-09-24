# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend dataset service.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.backend.services.dataset_service import DatasetService, get_dataset_service
from pyrit.models import SeedDatasetSummary


@pytest.fixture
def mock_memory():
    """Create a mock memory instance."""
    memory = MagicMock()
    memory.get_seed_dataset_names.return_value = []
    memory.get_seed_dataset_summaries.return_value = []
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
        assert all(not item.loaded for item in result.items)
        assert all(item.provider_available for item in result.items)

    async def test_list_datasets_includes_memory_summaries(self, dataset_service, mock_memory):
        mock_memory.get_seed_dataset_summaries.return_value = [
            SeedDatasetSummary(
                dataset_name="harmbench",
                logical_examples=3,
                seed_pieces=4,
                objectives=1,
                modalities=("image_path", "text"),
                harm_categories=("hate", "violence"),
                has_unlabeled_harm_categories=True,
            ),
            SeedDatasetSummary(
                dataset_name=None,
                logical_examples=1,
                seed_pieces=2,
                objectives=0,
                modalities=("text",),
                harm_categories=(),
                has_unlabeled_harm_categories=True,
            ),
        ]
        with patch(
            "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
            new_callable=AsyncMock,
            return_value=["harmbench", "provider_only"],
        ):
            result = await dataset_service.list_datasets_async()

        loaded = next(item for item in result.items if item.name == "harmbench")
        assert loaded.selection_key == "dataset:named:harmbench"
        assert loaded.loaded is True
        assert loaded.provider_available is True
        assert loaded.logical_examples == 3
        assert loaded.seed_pieces == 4
        assert loaded.objectives == 1
        assert loaded.modalities == ["image_path", "text"]
        assert loaded.harm_categories == ["hate", "violence"]
        assert loaded.has_unlabeled_harm_categories is True

        provider_only = next(item for item in result.items if item.name == "provider_only")
        assert provider_only.loaded is False
        assert provider_only.provider_available is True
        assert provider_only.logical_examples is None

        unnamed = next(item for item in result.items if item.name == "(unnamed)")
        assert unnamed.selection_key == "dataset:unnamed"
        assert unnamed.loaded is True
        assert unnamed.provider_available is False
        assert unnamed.logical_examples == 1

    async def test_list_datasets_selection_keys_use_distinct_namespaces(self, dataset_service, mock_memory):
        """A stored dataset named __unnamed__ and the unnamed population share no key."""
        mock_memory.get_seed_dataset_summaries.return_value = [
            SeedDatasetSummary(
                dataset_name="__unnamed__",
                logical_examples=1,
                seed_pieces=1,
                objectives=0,
                modalities=("text",),
                harm_categories=(),
                has_unlabeled_harm_categories=False,
            ),
            SeedDatasetSummary(
                dataset_name=None,
                logical_examples=2,
                seed_pieces=2,
                objectives=0,
                modalities=("url",),
                harm_categories=(),
                has_unlabeled_harm_categories=True,
            ),
        ]
        with patch(
            "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await dataset_service.list_datasets_async()

        selection_keys = {item.selection_key for item in result.items}
        assert selection_keys == {"dataset:named:__unnamed__", "dataset:unnamed"}

        named_unnamed = next(item for item in result.items if item.name == "__unnamed__")
        assert named_unnamed.selection_key == "dataset:named:__unnamed__"
        assert named_unnamed.loaded is True

        unnamed = next(item for item in result.items if item.name == "(unnamed)")
        assert unnamed.selection_key == "dataset:unnamed"
        assert unnamed.loaded is True
        assert unnamed.seed_pieces == 2

    async def test_list_datasets_never_surfaces_an_empty_dataset_name(self, dataset_service, mock_memory):
        """Seeds with an empty name filter nothing, so they fold into the unnamed population."""
        mock_memory.get_seed_dataset_summaries.return_value = [
            SeedDatasetSummary(
                dataset_name="",
                logical_examples=2,
                seed_pieces=3,
                objectives=1,
                modalities=("text",),
                harm_categories=("hate",),
                has_unlabeled_harm_categories=False,
            ),
            SeedDatasetSummary(
                dataset_name=None,
                logical_examples=1,
                seed_pieces=2,
                objectives=0,
                modalities=("text",),
                harm_categories=(),
                has_unlabeled_harm_categories=True,
            ),
        ]
        with patch(
            "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
            new_callable=AsyncMock,
            return_value=["harmbench"],
        ):
            result = await dataset_service.list_datasets_async()

        # No named choice for the empty name, and no key that would resolve to "all datasets".
        assert all(item.name for item in result.items if item.name != "(unnamed)")
        assert all(item.selection_key.startswith("dataset:named:") for item in result.items if item.name != "(unnamed)")

        # The empty-name seeds are represented under the unnamed population.
        unnamed = next(item for item in result.items if item.name == "(unnamed)")
        assert unnamed.selection_key == "dataset:unnamed"
        assert unnamed.seed_pieces == 5
        assert unnamed.logical_examples == 3
        assert unnamed.objectives == 1
        assert unnamed.modalities == ["text"]
        assert unnamed.harm_categories == ["hate"]
        assert unnamed.has_unlabeled_harm_categories is True

    async def test_list_datasets_keeps_unnamed_population_for_whitespace_collation(self, dataset_service, mock_memory):
        """A whitespace-only name normalized by the memory query remains the unnamed selection."""
        mock_memory.get_seed_dataset_summaries.return_value = [
            SeedDatasetSummary(
                dataset_name=None,
                logical_examples=1,
                seed_pieces=2,
                objectives=1,
                modalities=("text",),
                harm_categories=(),
                has_unlabeled_harm_categories=True,
            )
        ]
        with patch(
            "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await dataset_service.list_datasets_async()

        assert len(result.items) == 1
        unnamed = result.items[0]
        assert unnamed.name == "(unnamed)"
        assert unnamed.selection_key == "dataset:unnamed"
        assert unnamed.seed_pieces == 2
        assert unnamed.objectives == 1

    async def test_list_datasets_loaded_only_matches_the_empty_memory_contract(self, dataset_service):
        """#2746: an empty memory is a valid empty response, even with providers registered."""
        with patch(
            "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
            new_callable=AsyncMock,
            return_value=["harmbench"],
        ):
            combined = await dataset_service.list_datasets_async()
            loaded_only = await dataset_service.list_datasets_async(loaded_only=True)

        # The legacy combined listing keeps provider names for existing clients.
        assert [item.name for item in combined.items] == ["harmbench"]
        # The memory-backed view is empty when memory is empty.
        assert loaded_only.items == []

    async def test_list_datasets_loaded_only_restricts_to_memory(self, dataset_service, mock_memory):
        mock_memory.get_seed_dataset_summaries.return_value = [
            SeedDatasetSummary(
                dataset_name="harmbench",
                logical_examples=3,
                seed_pieces=4,
                objectives=1,
                modalities=("text",),
                harm_categories=("hate",),
                has_unlabeled_harm_categories=True,
            ),
            SeedDatasetSummary(
                dataset_name=None,
                logical_examples=1,
                seed_pieces=2,
                objectives=0,
                modalities=("text",),
                harm_categories=(),
                has_unlabeled_harm_categories=True,
            ),
        ]
        with patch(
            "pyrit.backend.services.dataset_service.SeedDatasetProvider.get_all_dataset_names_async",
            new_callable=AsyncMock,
            return_value=["harmbench", "provider_only"],
        ):
            result = await dataset_service.list_datasets_async(loaded_only=True)

        assert [item.name for item in result.items] == ["harmbench", "(unnamed)"]
        assert all(item.loaded for item in result.items)
        assert all(item.provider_available for item in result.items if item.name != "(unnamed)")

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
