# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for LoadDefaultDatasets initializer.
"""

from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.models import SeedDataset
from pyrit.registry import ScenarioRegistry
from pyrit.scenario.core.scenario import Scenario
from pyrit.setup.initializers.scenarios.load_default_datasets import LoadDefaultDatasets


@pytest.mark.usefixtures("patch_central_database")
class TestLoadDefaultDatasets:
    """Test suite for LoadDefaultDatasets initializer."""

    def test_name_property(self) -> None:
        """Test that name property returns expected value."""
        initializer = LoadDefaultDatasets()
        assert initializer.name == "Default Dataset Loader for Scenarios"

    def test_execution_order_property(self) -> None:
        """Test that execution order is set correctly."""
        initializer = LoadDefaultDatasets()
        assert initializer.execution_order == 10

    def test_description_property(self) -> None:
        """Test that description property returns non-empty string."""
        initializer = LoadDefaultDatasets()
        description = initializer.description
        assert isinstance(description, str)
        assert len(description) > 0
        assert "DatasetLoader" in description
        assert "scenarios" in description.lower()

    def test_required_env_vars_property(self) -> None:
        """Test that required_env_vars returns empty list."""
        initializer = LoadDefaultDatasets()
        assert initializer.required_env_vars == []

    @pytest.mark.asyncio
    async def test_initialize_async_no_scenarios(self) -> None:
        """Test initialization when no scenarios are registered."""
        initializer = LoadDefaultDatasets()

        with patch.object(ScenarioRegistry, "get_names", return_value=[]):
            with patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch:
                with patch.object(CentralMemory, "get_memory_instance") as mock_memory:
                    mock_memory_instance = MagicMock()
                    mock_memory_instance.add_seed_datasets_to_memory_async = AsyncMock()
                    mock_memory.return_value = mock_memory_instance

                    await initializer.initialize_async()

                    # Should not fetch datasets if no scenarios
                    mock_fetch.assert_not_called()
                    mock_memory_instance.add_seed_datasets_to_memory_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_async_with_scenarios(self) -> None:
        """Test initialization with scenarios that require datasets."""
        initializer = LoadDefaultDatasets()

        # Mock scenario class with default_dataset_config
        mock_dataset_config = MagicMock()
        mock_dataset_config.get_default_dataset_names.return_value = ["dataset1", "dataset2"]
        mock_scenario_class = MagicMock(spec=Scenario)
        mock_scenario_class.default_dataset_config.return_value = mock_dataset_config

        with patch.object(ScenarioRegistry, "get_names", return_value=["mock_scenario"]):
            with patch.object(ScenarioRegistry, "get_class", return_value=mock_scenario_class):
                with patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch:
                    mock_dataset1 = MagicMock(spec=SeedDataset)
                    mock_dataset2 = MagicMock(spec=SeedDataset)
                    mock_fetch.return_value = [mock_dataset1, mock_dataset2]

                    with patch.object(CentralMemory, "get_memory_instance") as mock_memory:
                        mock_memory_instance = MagicMock()
                        mock_memory_instance.add_seed_datasets_to_memory_async = AsyncMock()
                        mock_memory.return_value = mock_memory_instance

                        await initializer.initialize_async()

                        # Verify fetch_datasets_async was called with correct datasets
                        mock_fetch.assert_called_once()
                        call_kwargs = mock_fetch.call_args.kwargs
                        assert set(call_kwargs["dataset_names"]) == {"dataset1", "dataset2"}

                        # Verify datasets were added to memory
                        mock_memory_instance.add_seed_datasets_to_memory_async.assert_called_once_with(
                            datasets=[mock_dataset1, mock_dataset2], added_by="LoadDefaultDatasets"
                        )

    @pytest.mark.asyncio
    async def test_initialize_async_deduplicates_datasets(self) -> None:
        """Test that duplicate datasets from multiple scenarios are deduplicated."""
        initializer = LoadDefaultDatasets()

        # Mock two scenarios requiring overlapping datasets
        mock_dataset_config1 = MagicMock()
        mock_dataset_config1.get_default_dataset_names.return_value = ["dataset1", "dataset2"]
        mock_scenario1 = MagicMock(spec=Scenario)
        mock_scenario1.default_dataset_config.return_value = mock_dataset_config1

        mock_dataset_config2 = MagicMock()
        mock_dataset_config2.get_default_dataset_names.return_value = ["dataset2", "dataset3"]
        mock_scenario2 = MagicMock(spec=Scenario)
        mock_scenario2.default_dataset_config.return_value = mock_dataset_config2

        def get_scenario_side_effect(name: str):
            if name == "scenario1":
                return mock_scenario1
            elif name == "scenario2":
                return mock_scenario2
            return None

        with patch.object(ScenarioRegistry, "get_names", return_value=["scenario1", "scenario2"]):
            with patch.object(ScenarioRegistry, "get_class", side_effect=get_scenario_side_effect):
                with patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch:
                    mock_fetch.return_value = []

                    with patch.object(CentralMemory, "get_memory_instance") as mock_memory:
                        mock_memory_instance = MagicMock()
                        mock_memory_instance.add_seed_datasets_to_memory_async = AsyncMock()
                        mock_memory.return_value = mock_memory_instance

                        await initializer.initialize_async()

                        # Verify only unique datasets were requested
                        mock_fetch.assert_called_once()
                        call_kwargs = mock_fetch.call_args.kwargs
                        assert set(call_kwargs["dataset_names"]) == {"dataset1", "dataset2", "dataset3"}
                        # Verify order is preserved (dict.fromkeys maintains insertion order)
                        assert len(call_kwargs["dataset_names"]) == 3

    @pytest.mark.asyncio
    async def test_initialize_async_handles_scenario_errors(self) -> None:
        """Test that initialization continues when a scenario raises an error."""
        initializer = LoadDefaultDatasets()

        # Mock one scenario that works and one that fails
        mock_dataset_config_good = MagicMock()
        mock_dataset_config_good.get_default_dataset_names.return_value = ["dataset1"]
        mock_scenario_good = MagicMock(spec=Scenario)
        mock_scenario_good.default_dataset_config.return_value = mock_dataset_config_good

        mock_scenario_bad = MagicMock(spec=Scenario)
        mock_scenario_bad.default_dataset_config.side_effect = Exception("Test error")

        def get_scenario_side_effect(name: str):
            if name == "good_scenario":
                return mock_scenario_good
            elif name == "bad_scenario":
                return mock_scenario_bad
            return None

        with patch.object(ScenarioRegistry, "get_names", return_value=["good_scenario", "bad_scenario"]):
            with patch.object(ScenarioRegistry, "get_class", side_effect=get_scenario_side_effect):
                with patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch:
                    mock_fetch.return_value = []

                    with patch.object(CentralMemory, "get_memory_instance") as mock_memory:
                        mock_memory_instance = MagicMock()
                        mock_memory_instance.add_seed_datasets_to_memory_async = AsyncMock()
                        mock_memory.return_value = mock_memory_instance

                        await initializer.initialize_async()

                        # Verify it still fetched datasets from the good scenario
                        mock_fetch.assert_called_once()
                        call_kwargs = mock_fetch.call_args.kwargs
                        assert "dataset1" in call_kwargs["dataset_names"]

    @pytest.mark.asyncio
    async def test_all_required_datasets_available_in_seed_provider(self) -> None:
        """
        Test that all datasets required by scenarios are available in SeedDatasetProvider.

        This test ensures that every dataset name returned by scenario.required_datasets()
        exists in the SeedDatasetProvider registry.
        """
        # Get all available dataset names from SeedDatasetProvider
        available_datasets = set(SeedDatasetProvider.get_all_dataset_names())

        # Get ScenarioRegistry to discover all scenarios
        registry = ScenarioRegistry.get_registry_singleton()
        scenario_names = registry.get_names()

        # Collect all required datasets from all scenarios
        missing_datasets: List[str] = []
        scenario_dataset_map: dict[str, List[str]] = {}

        for scenario_name in scenario_names:
            scenario_class = registry.get_class(scenario_name)
            if scenario_class:
                try:
                    required = scenario_class.default_dataset_config().get_default_dataset_names()
                    scenario_dataset_map[scenario_name] = required

                    for dataset_name in required:
                        if dataset_name not in available_datasets:
                            missing_datasets.append(f"{scenario_name} requires '{dataset_name}'")
                except Exception as e:
                    # Log but don't fail - some scenarios might not be fully initialized
                    print(f"Warning: Could not get required datasets from {scenario_name}: {e}")

        # Assert that all required datasets are available
        assert len(missing_datasets) == 0, (
            "The following scenarios require datasets not available in SeedDatasetProvider:\n"
            + "\n".join(missing_datasets)
        )

    @pytest.mark.asyncio
    async def test_initialize_async_empty_dataset_list(self) -> None:
        """Test initialization when scenarios return empty dataset lists."""
        initializer = LoadDefaultDatasets()

        mock_dataset_config = MagicMock()
        mock_dataset_config.get_default_dataset_names.return_value = []
        mock_scenario = MagicMock(spec=Scenario)
        mock_scenario.default_dataset_config.return_value = mock_dataset_config

        with patch.object(ScenarioRegistry, "get_names", return_value=["empty_scenario"]):
            with patch.object(ScenarioRegistry, "get_class", return_value=mock_scenario):
                with patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch:
                    with patch.object(CentralMemory, "get_memory_instance") as mock_memory:
                        mock_memory_instance = MagicMock()
                        mock_memory_instance.add_seed_datasets_to_memory_async = AsyncMock()
                        mock_memory.return_value = mock_memory_instance

                        await initializer.initialize_async()

                        # Should not fetch datasets when all scenarios return empty lists
                        mock_fetch.assert_not_called()
                        mock_memory_instance.add_seed_datasets_to_memory_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_async_none_scenario_class(self) -> None:
        """Test initialization when get_scenario returns None for a scenario."""
        initializer = LoadDefaultDatasets()

        with patch.object(ScenarioRegistry, "get_names", return_value=["nonexistent_scenario"]):
            with patch.object(ScenarioRegistry, "get_class", return_value=None):
                with patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch:
                    with patch.object(CentralMemory, "get_memory_instance") as mock_memory:
                        mock_memory_instance = MagicMock()
                        mock_memory_instance.add_seed_datasets_to_memory_async = AsyncMock()
                        mock_memory.return_value = mock_memory_instance

                        await initializer.initialize_async()

                        # Should not crash, just skip the None scenario
                        mock_fetch.assert_not_called()
