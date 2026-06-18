# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for LoadDefaultDatasets initializer.
"""

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.models import SeedDataset
from pyrit.prompt_target import PromptTarget
from pyrit.registry import ScenarioRegistry, TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.setup.initializers.components.scenario_techniques import build_scenario_technique_factories
from pyrit.setup.initializers.scenarios.load_default_datasets import LoadDefaultDatasets


@pytest.fixture
def populated_technique_registry():
    """Populate the technique + target registries so scenario metadata building succeeds."""
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()

    adv_target = MagicMock(spec=PromptTarget)
    adv_target.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().register_instance(adv_target, name="adversarial_chat")

    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_scenario_technique_factories())
    yield
    AttackTechniqueRegistry.reset_instance()
    TargetRegistry.reset_instance()


@dataclass
class _FakeMetadata:
    registry_name: str
    default_datasets: tuple[str, ...] = field(default_factory=tuple)


@pytest.mark.usefixtures("patch_central_database")
class TestLoadDefaultDatasets:
    """Test suite for LoadDefaultDatasets initializer."""

    def test_description_property(self) -> None:
        """Test that description property returns non-empty string."""
        initializer = LoadDefaultDatasets()
        description = initializer.description
        assert isinstance(description, str)
        assert len(description) > 0
        assert "dataset" in description.lower()
        assert "scenarios" in description.lower()

    def test_required_env_vars_property(self) -> None:
        """Test that required_env_vars returns empty list."""
        initializer = LoadDefaultDatasets()
        assert initializer.required_env_vars == []

    async def test_initialize_async_no_scenarios(self) -> None:
        """Test initialization when no scenarios are registered."""
        initializer = LoadDefaultDatasets()

        with patch.object(ScenarioRegistry, "list_metadata", return_value=[]):
            with patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch:
                with patch.object(CentralMemory, "get_memory_instance") as mock_memory:
                    mock_memory_instance = MagicMock()
                    mock_memory_instance.add_seed_datasets_to_memory_async = AsyncMock()
                    mock_memory.return_value = mock_memory_instance

                    await initializer.initialize_async()

                    mock_fetch.assert_not_called()
                    mock_memory_instance.add_seed_datasets_to_memory_async.assert_not_called()

    async def test_initialize_async_with_scenarios(self) -> None:
        """Test initialization with scenarios that require datasets."""
        initializer = LoadDefaultDatasets()

        metadata = [_FakeMetadata(registry_name="mock_scenario", default_datasets=("dataset1", "dataset2"))]

        with patch.object(ScenarioRegistry, "list_metadata", return_value=metadata):
            with patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch:
                mock_dataset1 = MagicMock(spec=SeedDataset)
                mock_dataset2 = MagicMock(spec=SeedDataset)
                mock_fetch.return_value = [mock_dataset1, mock_dataset2]

                with patch.object(CentralMemory, "get_memory_instance") as mock_memory:
                    mock_memory_instance = MagicMock()
                    mock_memory_instance.add_seed_datasets_to_memory_async = AsyncMock()
                    mock_memory.return_value = mock_memory_instance

                    await initializer.initialize_async()

                    mock_fetch.assert_called_once()
                    call_kwargs = mock_fetch.call_args.kwargs
                    assert set(call_kwargs["dataset_names"]) == {"dataset1", "dataset2"}

                    mock_memory_instance.add_seed_datasets_to_memory_async.assert_called_once_with(
                        datasets=[mock_dataset1, mock_dataset2], added_by="LoadDefaultDatasets"
                    )

    async def test_initialize_async_deduplicates_datasets(self) -> None:
        """Test that duplicate datasets from multiple scenarios are deduplicated."""
        initializer = LoadDefaultDatasets()

        metadata = [
            _FakeMetadata(registry_name="scenario1", default_datasets=("dataset1", "dataset2")),
            _FakeMetadata(registry_name="scenario2", default_datasets=("dataset2", "dataset3")),
        ]

        with patch.object(ScenarioRegistry, "list_metadata", return_value=metadata):
            with patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = []

                with patch.object(CentralMemory, "get_memory_instance") as mock_memory:
                    mock_memory_instance = MagicMock()
                    mock_memory_instance.add_seed_datasets_to_memory_async = AsyncMock()
                    mock_memory.return_value = mock_memory_instance

                    await initializer.initialize_async()

                    mock_fetch.assert_called_once()
                    call_kwargs = mock_fetch.call_args.kwargs
                    assert set(call_kwargs["dataset_names"]) == {"dataset1", "dataset2", "dataset3"}
                    assert len(call_kwargs["dataset_names"]) == 3

    async def test_all_required_datasets_available_in_seed_provider(self, populated_technique_registry) -> None:
        """
        Test that all datasets required by scenarios are available in SeedDatasetProvider.

        This test ensures that every dataset name listed in scenario metadata exists in
        the SeedDatasetProvider registry.
        """
        available_datasets = set(await SeedDatasetProvider.get_all_dataset_names_async())

        # Patch OpenAIChatTarget at the fallback construction site so registry
        # introspection does not depend on OPENAI_CHAT_MODEL or other env vars.
        from pyrit.models.identifiers import ComponentIdentifier
        from pyrit.score import TrueFalseScorer

        fallback_target = MagicMock()
        fallback_target.get_identifier.return_value = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target.openai.openai_chat_target",
        )
        fallback_scorer = MagicMock(spec=TrueFalseScorer)
        with (
            patch("pyrit.scenario.core.scenario_target_defaults.OpenAIChatTarget", return_value=fallback_target),
            patch(
                "pyrit.scenario.core.scenario.Scenario._get_default_objective_scorer",
                return_value=fallback_scorer,
            ),
        ):
            registry = ScenarioRegistry.get_registry_singleton()
            registry._metadata_cache = None  # force rebuild under the patch
            metadata_list = list(registry.list_metadata())

        missing_datasets: list[str] = []
        for metadata in metadata_list:
            missing_datasets.extend(
                f"{metadata.registry_name} requires '{dataset_name}'"
                for dataset_name in metadata.default_datasets
                if dataset_name not in available_datasets
            )

        assert len(missing_datasets) == 0, (
            "The following scenarios require datasets not available in SeedDatasetProvider:\n"
            + "\n".join(missing_datasets)
        )

    async def test_initialize_async_empty_dataset_list(self) -> None:
        """Test initialization when scenarios return empty dataset lists."""
        initializer = LoadDefaultDatasets()

        metadata = [_FakeMetadata(registry_name="empty_scenario", default_datasets=())]

        with patch.object(ScenarioRegistry, "list_metadata", return_value=metadata):
            with patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch:
                with patch.object(CentralMemory, "get_memory_instance") as mock_memory:
                    mock_memory_instance = MagicMock()
                    mock_memory_instance.add_seed_datasets_to_memory_async = AsyncMock()
                    mock_memory.return_value = mock_memory_instance

                    await initializer.initialize_async()

                    mock_fetch.assert_not_called()
