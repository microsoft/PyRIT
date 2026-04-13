# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the DatasetConfiguration class."""

import random
from unittest.mock import MagicMock, patch

import pytest

from pyrit.models import SeedGroup, SeedObjective, SeedPrompt
from pyrit.scenario.core.dataset_configuration import (
    EXPLICIT_SEED_GROUPS_KEY,
    DatasetConfiguration,
)


@pytest.fixture
def sample_seed_group() -> SeedGroup:
    """Create a sample SeedGroup for testing."""
    return SeedGroup(
        seeds=[
            SeedObjective(value="Test objective"),
            SeedPrompt(value="Test prompt"),
        ]
    )


@pytest.fixture
def sample_seed_groups(sample_seed_group: SeedGroup) -> list:
    """Create multiple sample SeedGroups for testing."""
    return [
        sample_seed_group,
        SeedGroup(
            seeds=[
                SeedObjective(value="Second objective"),
                SeedPrompt(value="Second prompt"),
            ]
        ),
        SeedGroup(
            seeds=[
                SeedObjective(value="Third objective"),
                SeedPrompt(value="Third prompt"),
            ]
        ),
    ]


class TestDatasetConfigurationInit:
    """Tests for DatasetConfiguration initialization."""

    def test_init_with_seed_groups_only(self, sample_seed_groups: list) -> None:
        """Test initialization with only seed_groups."""
        config = DatasetConfiguration(seed_groups=sample_seed_groups)

        assert config._seed_groups == sample_seed_groups
        assert config._dataset_names is None
        assert config.max_dataset_size is None
        assert config._scenario_composites is None

    def test_init_with_dataset_names_only(self) -> None:
        """Test initialization with only dataset_names."""
        dataset_names = ["dataset1", "dataset2"]
        config = DatasetConfiguration(dataset_names=dataset_names)

        assert config._seed_groups is None
        assert config._dataset_names == dataset_names
        assert config.max_dataset_size is None

    def test_init_with_both_seed_groups_and_dataset_names_raises_error(self, sample_seed_groups: list) -> None:
        """Test that setting both seed_groups and dataset_names raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            DatasetConfiguration(
                seed_groups=sample_seed_groups,
                dataset_names=["dataset1"],
            )

        assert "Only one of 'seed_groups' or 'dataset_names' can be set" in str(exc_info.value)

    def test_init_with_max_dataset_size(self, sample_seed_groups: list) -> None:
        """Test initialization with max_dataset_size."""
        config = DatasetConfiguration(seed_groups=sample_seed_groups, max_dataset_size=2)

        assert config.max_dataset_size == 2

    def test_init_with_max_dataset_size_zero_raises_error(self) -> None:
        """Test that max_dataset_size=0 raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            DatasetConfiguration(dataset_names=["dataset1"], max_dataset_size=0)

        assert "'max_dataset_size' must be a positive integer" in str(exc_info.value)

    def test_init_with_max_dataset_size_negative_raises_error(self) -> None:
        """Test that negative max_dataset_size raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            DatasetConfiguration(dataset_names=["dataset1"], max_dataset_size=-1)

        assert "'max_dataset_size' must be a positive integer" in str(exc_info.value)

    def test_init_copies_seed_groups_to_prevent_mutation(self, sample_seed_groups: list) -> None:
        """Test that the constructor copies seed_groups list to prevent external mutation."""
        original_list = list(sample_seed_groups)
        config = DatasetConfiguration(seed_groups=sample_seed_groups)

        # Mutate the original list
        sample_seed_groups.append(SeedGroup(seeds=[SeedObjective(value="New objective")]))

        # Config should still have the original length
        assert len(config._seed_groups) == len(original_list)

    def test_init_copies_dataset_names_to_prevent_mutation(self) -> None:
        """Test that the constructor copies dataset_names list to prevent external mutation."""
        dataset_names = ["dataset1", "dataset2"]
        config = DatasetConfiguration(dataset_names=dataset_names)

        # Mutate the original list
        dataset_names.append("dataset3")

        # Config should still have the original length
        assert len(config._dataset_names) == 2

    def test_init_with_scenario_composites(self, sample_seed_groups: list) -> None:
        """Test initialization with scenario_composites."""
        mock_composites = [MagicMock(), MagicMock()]
        config = DatasetConfiguration(
            seed_groups=sample_seed_groups,
            scenario_composites=mock_composites,
        )

        assert config._scenario_composites == mock_composites

    def test_init_with_no_data_source(self) -> None:
        """Test initialization with neither seed_groups nor dataset_names."""
        config = DatasetConfiguration()

        assert config._seed_groups is None
        assert config._dataset_names is None


@pytest.mark.usefixtures("patch_central_database")
class TestDatasetConfigurationGetSeedGroups:
    """Tests for DatasetConfiguration.get_seed_groups method."""

    def test_get_seed_groups_with_explicit_seed_groups(self, sample_seed_groups: list) -> None:
        """Test get_seed_groups returns explicit seed_groups under special key."""
        config = DatasetConfiguration(seed_groups=sample_seed_groups)

        result = config.get_seed_groups()

        assert EXPLICIT_SEED_GROUPS_KEY in result
        assert result[EXPLICIT_SEED_GROUPS_KEY] == sample_seed_groups

    def test_get_seed_groups_with_dataset_names(self, sample_seed_groups: list) -> None:
        """Test get_seed_groups loads from memory when dataset_names is set."""
        config = DatasetConfiguration(dataset_names=["test_dataset"])

        with patch.object(config, "_load_seed_groups_for_dataset", return_value=sample_seed_groups):
            result = config.get_seed_groups()

        assert "test_dataset" in result
        assert result["test_dataset"] == sample_seed_groups

    def test_get_seed_groups_with_multiple_dataset_names(self, sample_seed_groups: list) -> None:
        """Test get_seed_groups loads multiple datasets from memory."""
        config = DatasetConfiguration(dataset_names=["dataset1", "dataset2"])

        def mock_load(*, dataset_name: str):
            return sample_seed_groups if dataset_name in ["dataset1", "dataset2"] else []

        with patch.object(config, "_load_seed_groups_for_dataset", side_effect=mock_load):
            result = config.get_seed_groups()

        assert "dataset1" in result
        assert "dataset2" in result

    def test_get_seed_groups_skips_empty_datasets_from_memory(self) -> None:
        """Test that empty datasets from memory are not included in results."""
        config = DatasetConfiguration(dataset_names=["populated", "empty"])

        def mock_load(*, dataset_name: str):
            if dataset_name == "populated":
                return [SeedGroup(seeds=[SeedObjective(value="obj")])]
            return []

        with patch.object(config, "_load_seed_groups_for_dataset", side_effect=mock_load):
            result = config.get_seed_groups()

        assert "populated" in result
        assert "empty" not in result

    def test_get_seed_groups_with_no_data_source_raises_error(self) -> None:
        """Test that get_seed_groups raises ValueError when no data source is configured."""
        config = DatasetConfiguration()

        with pytest.raises(ValueError) as exc_info:
            config.get_seed_groups()

        assert "DatasetConfiguration has no seed_groups" in str(exc_info.value)

    def test_get_seed_groups_applies_max_dataset_size_per_dataset(self, sample_seed_groups: list) -> None:
        """Test that max_dataset_size is applied per dataset."""
        config = DatasetConfiguration(seed_groups=sample_seed_groups, max_dataset_size=1)

        # Set seed for deterministic random sampling
        random.seed(42)
        result = config.get_seed_groups()

        assert len(result[EXPLICIT_SEED_GROUPS_KEY]) == 1

    def test_get_seed_groups_with_empty_seed_groups_list_raises_error(self) -> None:
        """Test that empty seed_groups list raises ValueError."""
        config = DatasetConfiguration(seed_groups=[])

        with pytest.raises(ValueError) as exc_info:
            config.get_seed_groups()

        assert "DatasetConfiguration has no seed_groups" in str(exc_info.value)


@pytest.mark.usefixtures("patch_central_database")
class TestDatasetConfigurationLoadSeedGroupsForDataset:
    """Tests for DatasetConfiguration._load_seed_groups_for_dataset method."""

    def test_load_seed_groups_for_dataset_calls_memory(self, sample_seed_groups: list) -> None:
        """Test that _load_seed_groups_for_dataset calls CentralMemory."""
        config = DatasetConfiguration(dataset_names=["test_dataset"])

        with patch("pyrit.scenario.core.dataset_configuration.CentralMemory") as mock_central_memory:
            mock_memory = MagicMock()
            mock_memory.get_seed_groups.return_value = sample_seed_groups
            mock_central_memory.get_memory_instance.return_value = mock_memory

            result = config._load_seed_groups_for_dataset(dataset_name="test_dataset")

        mock_memory.get_seed_groups.assert_called_once_with(dataset_name="test_dataset")
        assert result == sample_seed_groups

    def test_load_seed_groups_for_dataset_returns_empty_list_when_none(self) -> None:
        """Test that _load_seed_groups_for_dataset returns empty list when memory returns None."""
        config = DatasetConfiguration(dataset_names=["nonexistent"])

        with patch("pyrit.scenario.core.dataset_configuration.CentralMemory") as mock_central_memory:
            mock_memory = MagicMock()
            mock_memory.get_seed_groups.return_value = None
            mock_central_memory.get_memory_instance.return_value = mock_memory

            result = config._load_seed_groups_for_dataset(dataset_name="nonexistent")

        assert result == []


@pytest.mark.usefixtures("patch_central_database")
class TestDatasetConfigurationGetAllSeedGroups:
    """Tests for DatasetConfiguration.get_all_seed_groups method."""

    def test_get_all_seed_groups_flattens_results(self, sample_seed_groups: list) -> None:
        """Test that get_all_seed_groups returns a flat list."""
        config = DatasetConfiguration(seed_groups=sample_seed_groups)

        result = config.get_all_seed_groups()

        assert isinstance(result, list)
        assert len(result) == len(sample_seed_groups)
        for group in sample_seed_groups:
            assert group in result

    def test_get_all_seed_groups_combines_multiple_datasets(self) -> None:
        """Test that get_all_seed_groups combines seed groups from multiple datasets."""
        config = DatasetConfiguration(dataset_names=["dataset1", "dataset2"])

        group1 = SeedGroup(seeds=[SeedObjective(value="obj1")])
        group2 = SeedGroup(seeds=[SeedObjective(value="obj2")])

        def mock_load(*, dataset_name: str):
            return [group1] if dataset_name == "dataset1" else [group2]

        with patch.object(config, "_load_seed_groups_for_dataset", side_effect=mock_load):
            result = config.get_all_seed_groups()

        assert len(result) == 2
        assert group1 in result
        assert group2 in result

    def test_get_all_seed_groups_raises_error_when_no_data_source(self) -> None:
        """Test that get_all_seed_groups raises ValueError when no data source is configured."""
        config = DatasetConfiguration()

        with pytest.raises(ValueError) as exc_info:
            config.get_all_seed_groups()

        assert "DatasetConfiguration has no seed_groups" in str(exc_info.value)


class TestDatasetConfigurationGetDefaultDatasetNames:
    """Tests for DatasetConfiguration.get_default_dataset_names method."""

    def test_get_default_dataset_names_returns_dataset_names(self) -> None:
        """Test that get_default_dataset_names returns configured dataset_names."""
        dataset_names = ["dataset1", "dataset2", "dataset3"]
        config = DatasetConfiguration(dataset_names=dataset_names)

        result = config.get_default_dataset_names()

        assert result == dataset_names

    def test_get_default_dataset_names_returns_copy(self) -> None:
        """Test that get_default_dataset_names returns a copy of the list."""
        dataset_names = ["dataset1", "dataset2"]
        config = DatasetConfiguration(dataset_names=dataset_names)

        result = config.get_default_dataset_names()
        result.append("dataset3")

        # Original should be unchanged
        assert len(config.get_default_dataset_names()) == 2

    def test_get_default_dataset_names_returns_empty_with_seed_groups(self, sample_seed_groups: list) -> None:
        """Test that get_default_dataset_names returns empty list when using explicit seed_groups."""
        config = DatasetConfiguration(seed_groups=sample_seed_groups)

        result = config.get_default_dataset_names()

        assert result == []

    def test_get_default_dataset_names_returns_empty_when_no_config(self) -> None:
        """Test that get_default_dataset_names returns empty list when nothing is configured."""
        config = DatasetConfiguration()

        result = config.get_default_dataset_names()

        assert result == []


class TestDatasetConfigurationApplyMaxDatasetSize:
    """Tests for DatasetConfiguration._apply_max_dataset_size method."""

    def test_apply_max_returns_original_when_none(self, sample_seed_groups: list) -> None:
        """Test that original list is returned when max_dataset_size is None."""
        config = DatasetConfiguration(seed_groups=sample_seed_groups)

        result = config._apply_max_dataset_size(sample_seed_groups)

        assert result == sample_seed_groups

    def test_apply_max_returns_original_when_under_limit(self, sample_seed_groups: list) -> None:
        """Test that original list is returned when length is under max_dataset_size."""
        config = DatasetConfiguration(seed_groups=sample_seed_groups, max_dataset_size=10)

        result = config._apply_max_dataset_size(sample_seed_groups)

        assert result == sample_seed_groups

    def test_apply_max_returns_original_when_equal_to_limit(self, sample_seed_groups: list) -> None:
        """Test that original list is returned when length equals max_dataset_size."""
        config = DatasetConfiguration(
            seed_groups=sample_seed_groups,
            max_dataset_size=len(sample_seed_groups),
        )

        result = config._apply_max_dataset_size(sample_seed_groups)

        assert result == sample_seed_groups

    def test_apply_max_returns_sample_when_over_limit(self, sample_seed_groups: list) -> None:
        """Test that a random sample is returned when length exceeds max_dataset_size."""
        config = DatasetConfiguration(seed_groups=sample_seed_groups, max_dataset_size=1)

        # Set seed for deterministic random sampling
        random.seed(42)
        result = config._apply_max_dataset_size(sample_seed_groups)

        assert len(result) == 1
        assert result[0] in sample_seed_groups

    def test_apply_max_returns_correct_sample_size(self) -> None:
        """Test that the sample size is exactly max_dataset_size."""
        large_seed_groups = [SeedGroup(seeds=[SeedObjective(value=f"obj{i}")]) for i in range(20)]
        config = DatasetConfiguration(seed_groups=large_seed_groups, max_dataset_size=5)

        result = config._apply_max_dataset_size(large_seed_groups)

        assert len(result) == 5
        for group in result:
            assert group in large_seed_groups


class TestDatasetConfigurationHasDataSource:
    """Tests for DatasetConfiguration.has_data_source method."""

    def test_has_data_source_true_with_seed_groups(self, sample_seed_groups: list) -> None:
        """Test that has_data_source returns True when seed_groups is set."""
        config = DatasetConfiguration(seed_groups=sample_seed_groups)

        assert config.has_data_source() is True

    def test_has_data_source_true_with_dataset_names(self) -> None:
        """Test that has_data_source returns True when dataset_names is set."""
        config = DatasetConfiguration(dataset_names=["dataset1"])

        assert config.has_data_source() is True

    def test_has_data_source_false_when_empty(self) -> None:
        """Test that has_data_source returns False when nothing is configured."""
        config = DatasetConfiguration()

        assert config.has_data_source() is False

    def test_has_data_source_true_with_empty_seed_groups_list(self) -> None:
        """Test that has_data_source returns True even with empty seed_groups list."""
        # Note: This tests the current behavior - an empty list is still "configured"
        config = DatasetConfiguration(seed_groups=[])

        assert config.has_data_source() is True


@pytest.mark.usefixtures("patch_central_database")
class TestDatasetConfigurationGetAllSeeds:
    """Tests for DatasetConfiguration.get_all_seeds method."""

    def test_get_all_seeds_raises_when_no_dataset_names(self) -> None:
        """Test that get_all_seeds raises ValueError when no dataset_names are configured."""
        config = DatasetConfiguration()

        with pytest.raises(ValueError, match="No dataset names configured"):
            config.get_all_seeds()

    def test_get_all_seeds_raises_when_seed_groups_configured(self, sample_seed_groups: list) -> None:
        """Test that get_all_seeds raises ValueError when seed_groups are configured instead of dataset_names."""
        config = DatasetConfiguration(seed_groups=sample_seed_groups)

        with pytest.raises(ValueError, match="No dataset names configured"):
            config.get_all_seeds()

    def test_get_all_seeds_returns_seeds_from_memory(self) -> None:
        """Test that get_all_seeds returns SeedPrompt objects from memory."""
        mock_seeds = [
            SeedPrompt(value="seed1", data_type="text"),
            SeedPrompt(value="seed2", data_type="text"),
        ]

        with patch("pyrit.scenario.core.dataset_configuration.CentralMemory") as mock_memory_class:
            mock_memory = MagicMock()
            mock_memory.get_seeds.return_value = mock_seeds
            mock_memory_class.get_memory_instance.return_value = mock_memory

            config = DatasetConfiguration(dataset_names=["test_dataset"])
            result = config.get_all_seeds()

            assert len(result) == 2
            assert result[0].value == "seed1"
            assert result[1].value == "seed2"
            mock_memory.get_seeds.assert_called_once_with(dataset_name="test_dataset")

    def test_get_all_seeds_aggregates_from_multiple_datasets(self) -> None:
        """Test that get_all_seeds aggregates seeds from all configured datasets."""
        seeds_dataset1 = [SeedPrompt(value="ds1_seed1", data_type="text")]
        seeds_dataset2 = [
            SeedPrompt(value="ds2_seed1", data_type="text"),
            SeedPrompt(value="ds2_seed2", data_type="text"),
        ]

        with patch("pyrit.scenario.core.dataset_configuration.CentralMemory") as mock_memory_class:
            mock_memory = MagicMock()
            mock_memory.get_seeds.side_effect = [seeds_dataset1, seeds_dataset2]
            mock_memory_class.get_memory_instance.return_value = mock_memory

            config = DatasetConfiguration(dataset_names=["dataset1", "dataset2"])
            result = config.get_all_seeds()

            assert len(result) == 3
            assert result[0].value == "ds1_seed1"
            assert result[1].value == "ds2_seed1"
            assert result[2].value == "ds2_seed2"
            assert mock_memory.get_seeds.call_count == 2

    def test_get_all_seeds_applies_max_dataset_size_per_dataset(self) -> None:
        """Test that get_all_seeds applies max_dataset_size sampling per dataset."""
        seeds = [SeedPrompt(value=f"seed{i}", data_type="text") for i in range(10)]

        with patch("pyrit.scenario.core.dataset_configuration.CentralMemory") as mock_memory_class:
            mock_memory = MagicMock()
            mock_memory.get_seeds.return_value = seeds
            mock_memory_class.get_memory_instance.return_value = mock_memory

            config = DatasetConfiguration(dataset_names=["dataset1"], max_dataset_size=3)
            result = config.get_all_seeds()

            assert len(result) == 3
            # All returned seeds should be from the original list
            for seed in result:
                assert seed in seeds

    def test_get_all_seeds_returns_all_when_under_max_size(self) -> None:
        """Test that get_all_seeds returns all seeds when count is under max_dataset_size."""
        seeds = [SeedPrompt(value=f"seed{i}", data_type="text") for i in range(3)]

        with patch("pyrit.scenario.core.dataset_configuration.CentralMemory") as mock_memory_class:
            mock_memory = MagicMock()
            mock_memory.get_seeds.return_value = seeds
            mock_memory_class.get_memory_instance.return_value = mock_memory

            config = DatasetConfiguration(dataset_names=["dataset1"], max_dataset_size=10)
            result = config.get_all_seeds()

            assert len(result) == 3

    def test_get_all_seeds_returns_empty_list_when_no_seeds_in_memory(self) -> None:
        """Test that get_all_seeds returns empty list when memory has no seeds."""
        with patch("pyrit.scenario.core.dataset_configuration.CentralMemory") as mock_memory_class:
            mock_memory = MagicMock()
            mock_memory.get_seeds.return_value = []
            mock_memory_class.get_memory_instance.return_value = mock_memory

            config = DatasetConfiguration(dataset_names=["empty_dataset"])
            result = config.get_all_seeds()

            assert result == []
