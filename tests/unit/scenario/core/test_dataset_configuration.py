# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the DatasetConfiguration base class and DatasetAttackConfiguration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.models import SeedAttackGroup, SeedGroup, SeedObjective, SeedPrompt
from pyrit.scenario.core.dataset_configuration import (
    INLINE_DATASET_NAME,
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    DatasetConfiguration,
    DatasetConstraintError,
    DatasetSourceKind,
    ResolvedDataset,
    forbid_inline_seeds,
    require_harm_categories,
    require_inline_seeds,
    require_min_size,
    require_nonempty,
    require_seed_type,
    restrict_dataset_names,
)

MEMORY_PATCH_TARGET = "pyrit.scenario.core.dataset_configuration.CentralMemory.get_memory_instance"
PROVIDER_PATCH_TARGET = "pyrit.datasets.seed_datasets.seed_dataset_provider.SeedDatasetProvider"


def resolved(
    *seeds: SeedObjective | SeedPrompt,
    source_kind: DatasetSourceKind = DatasetSourceKind.MEMORY,
    dataset_names: tuple[str, ...] = (),
) -> ResolvedDataset:
    """Build a ResolvedDataset from inline seeds for validator tests."""
    return ResolvedDataset(seeds=list(seeds), source_kind=source_kind, dataset_names=dataset_names)


@pytest.fixture
def mock_memory() -> MagicMock:
    """A stand-in CentralMemory whose ``get_seeds`` returns nothing by default."""
    memory = MagicMock()
    memory.get_seeds.return_value = []
    memory.get_seed_groups.return_value = []
    memory.add_seed_datasets_to_memory_async = AsyncMock()
    return memory


@pytest.fixture(autouse=True)
def patch_memory(mock_memory: MagicMock):
    """Patch ``CentralMemory.get_memory_instance`` so configs resolve against ``mock_memory``."""
    with patch(MEMORY_PATCH_TARGET, return_value=mock_memory):
        yield mock_memory


@pytest.fixture
def sample_seed_group() -> SeedGroup:
    """A SeedGroup carrying exactly one objective and one prompt."""
    return SeedGroup(seeds=[SeedObjective(value="Test objective"), SeedPrompt(value="Test prompt")])


@pytest.fixture
def sample_seed_groups() -> list[SeedGroup]:
    """Three distinct SeedGroups, each with one objective and one prompt."""
    return [
        SeedGroup(seeds=[SeedObjective(value="o1"), SeedPrompt(value="p1")]),
        SeedGroup(seeds=[SeedObjective(value="o2"), SeedPrompt(value="p2")]),
        SeedGroup(seeds=[SeedObjective(value="o3"), SeedPrompt(value="p3")]),
    ]


def make_objectives(*values: str) -> list[SeedObjective]:
    """Build a list of SeedObjective seeds (each becomes its own attack group)."""
    return [SeedObjective(value=v) for v in values]


class TestDatasetConfigurationInit:
    """Construction, source-exclusivity, and defensive copying."""

    def test_init_with_seeds_only(self) -> None:
        seeds = make_objectives("a", "b")
        config = DatasetConfiguration(seeds=seeds)
        assert config._seeds == seeds
        assert config._seed_groups is None
        assert config._dataset_names is None

    def test_init_with_seed_groups_only(self, sample_seed_groups: list[SeedGroup]) -> None:
        config = DatasetConfiguration(seed_groups=sample_seed_groups)
        assert config._seed_groups == sample_seed_groups
        assert config._seeds is None
        assert config._dataset_names is None
        assert config.max_dataset_size is None

    def test_init_with_dataset_names_only(self) -> None:
        config = DatasetConfiguration(dataset_names=["dataset1", "dataset2"])
        assert config._dataset_names == ["dataset1", "dataset2"]
        assert config._seeds is None
        assert config._seed_groups is None

    def test_init_defaults_to_auto_fetch(self) -> None:
        config = DatasetConfiguration(dataset_names=["d1"])
        assert config._auto_fetch is True

    def test_init_auto_fetch_can_be_disabled(self) -> None:
        config = DatasetConfiguration(dataset_names=["d1"], auto_fetch=False)
        assert config._auto_fetch is False

    def test_init_with_two_sources_raises(self, sample_seed_groups: list[SeedGroup]) -> None:
        with pytest.raises(ValueError, match="Only one of 'seeds', 'seed_groups', or 'dataset_names'"):
            DatasetConfiguration(seed_groups=sample_seed_groups, dataset_names=["d1"])

    def test_init_with_three_sources_raises(self, sample_seed_groups: list[SeedGroup]) -> None:
        with pytest.raises(ValueError, match="Only one of"):
            DatasetConfiguration(
                seeds=make_objectives("a"),
                seed_groups=sample_seed_groups,
                dataset_names=["d1"],
            )

    def test_init_with_max_dataset_size(self, sample_seed_groups: list[SeedGroup]) -> None:
        config = DatasetConfiguration(seed_groups=sample_seed_groups, max_dataset_size=2)
        assert config.max_dataset_size == 2

    def test_init_with_max_dataset_size_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            DatasetConfiguration(dataset_names=["d1"], max_dataset_size=0)

    def test_init_with_max_dataset_size_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            DatasetConfiguration(dataset_names=["d1"], max_dataset_size=-1)

    def test_init_copies_seed_groups_to_prevent_mutation(self, sample_seed_groups: list[SeedGroup]) -> None:
        config = DatasetConfiguration(seed_groups=sample_seed_groups)
        sample_seed_groups.append(SeedGroup(seeds=[SeedObjective(value="extra")]))
        assert config._seed_groups is not None
        assert len(config._seed_groups) == 3

    def test_init_copies_dataset_names_to_prevent_mutation(self) -> None:
        names = ["d1", "d2"]
        config = DatasetConfiguration(dataset_names=names)
        names.append("d3")
        assert config._dataset_names == ["d1", "d2"]

    def test_init_copies_seeds_to_prevent_mutation(self) -> None:
        seeds = make_objectives("a", "b")
        config = DatasetConfiguration(seeds=seeds)
        seeds.append(SeedObjective(value="c"))
        assert config._seeds is not None
        assert len(config._seeds) == 2


class TestDatasetNamesProperty:
    """The ``dataset_names`` property (replaces the deprecated getter)."""

    def test_returns_configured_names(self) -> None:
        config = DatasetConfiguration(dataset_names=["d1", "d2"])
        assert config.dataset_names == ["d1", "d2"]

    def test_returns_copy(self) -> None:
        config = DatasetConfiguration(dataset_names=["d1"])
        config.dataset_names.append("mutated")
        assert config.dataset_names == ["d1"]

    def test_empty_with_seed_groups(self, sample_seed_groups: list[SeedGroup]) -> None:
        config = DatasetConfiguration(seed_groups=sample_seed_groups)
        assert config.dataset_names == []

    def test_empty_with_no_source(self) -> None:
        assert DatasetConfiguration().dataset_names == []


class TestResolutionErrors:
    """Loud failure branches in base resolution, reached via ``DatasetAttackConfiguration``."""

    async def test_empty_inline_raises(self) -> None:
        config = DatasetAttackConfiguration(seeds=[])
        with pytest.raises(DatasetConstraintError, match="empty"):
            await config.get_seed_attack_groups_async()

    async def test_raises_loudly_when_still_empty_after_fetch(self) -> None:
        config = DatasetAttackConfiguration(dataset_names=["d1"])
        with patch.object(config, "_fetch_dataset_async", new=AsyncMock()):
            with pytest.raises(DatasetConstraintError, match="could not be loaded"):
                await config.get_seed_attack_groups_async()

    async def test_raises_when_empty_and_auto_fetch_disabled(self) -> None:
        config = DatasetAttackConfiguration(dataset_names=["d1"], auto_fetch=False)
        with pytest.raises(DatasetConstraintError, match="auto_fetch is disabled"):
            await config.get_seed_attack_groups_async()

    async def test_dataset_constraint_error_is_value_error(self) -> None:
        config = DatasetAttackConfiguration(dataset_names=["d1"], auto_fetch=False)
        with pytest.raises(ValueError):
            await config.get_seed_attack_groups_async()


class TestGetSeedAttackGroupsAsync:
    """``DatasetAttackConfiguration.get_seed_attack_groups_async`` (flat, global sample)."""

    async def test_inline_seed_groups_to_attack_groups(self, sample_seed_groups: list[SeedGroup]) -> None:
        config = DatasetAttackConfiguration(seed_groups=sample_seed_groups)
        groups = await config.get_seed_attack_groups_async()
        assert len(groups) == 3
        assert all(isinstance(g, SeedAttackGroup) for g in groups)

    async def test_inline_seeds_built_into_groups(self) -> None:
        config = DatasetAttackConfiguration(seeds=make_objectives("a", "b"))
        groups = await config.get_seed_attack_groups_async()
        assert len(groups) == 2
        assert all(isinstance(g, SeedAttackGroup) for g in groups)

    async def test_from_memory(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.return_value = make_objectives("a", "b", "c")
        config = DatasetAttackConfiguration(dataset_names=["d1"])
        groups = await config.get_seed_attack_groups_async()
        assert len(groups) == 3

    async def test_applies_max_dataset_size_globally(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.return_value = make_objectives("a", "b", "c", "d")
        config = DatasetAttackConfiguration(dataset_names=["d1"], max_dataset_size=2)
        groups = await config.get_seed_attack_groups_async()
        assert len(groups) == 2

    async def test_empty_raises(self) -> None:
        config = DatasetAttackConfiguration(dataset_names=["d1"], auto_fetch=False)
        with pytest.raises(DatasetConstraintError):
            await config.get_seed_attack_groups_async()

    async def test_auto_fetch_when_memory_empty(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.side_effect = [[], make_objectives("a")]
        config = DatasetAttackConfiguration(dataset_names=["d1"])
        with patch.object(config, "_fetch_dataset_async", new=AsyncMock()) as mock_fetch:
            groups = await config.get_seed_attack_groups_async()
        assert len(groups) == 1
        mock_fetch.assert_awaited_once_with(dataset_name="d1")


class TestGetAttackGroupsByDatasetAsync:
    """``get_attack_groups_by_dataset_async`` (keyed by dataset, global sample)."""

    async def test_inline_uses_inline_label(self, sample_seed_groups: list[SeedGroup]) -> None:
        config = DatasetAttackConfiguration(seed_groups=sample_seed_groups)
        result = await config.get_attack_groups_by_dataset_async()
        assert set(result.keys()) == {INLINE_DATASET_NAME}
        assert len(result[INLINE_DATASET_NAME]) == 3

    async def test_keyed_per_dataset(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.side_effect = [make_objectives("a", "b"), make_objectives("c")]
        config = DatasetAttackConfiguration(dataset_names=["d1", "d2"])
        result = await config.get_attack_groups_by_dataset_async()
        assert set(result.keys()) == {"d1", "d2"}
        assert len(result["d1"]) == 2
        assert len(result["d2"]) == 1

    async def test_max_sample_is_a_single_global_budget(self, mock_memory: MagicMock) -> None:
        # A single config now applies max_dataset_size globally across datasets, not per dataset.
        mock_memory.get_seeds.side_effect = [make_objectives("a", "b", "c"), make_objectives("d", "e", "f")]
        config = DatasetAttackConfiguration(dataset_names=["d1", "d2"], max_dataset_size=2)
        result = await config.get_attack_groups_by_dataset_async()
        assert sum(len(groups) for groups in result.values()) == 2

    async def test_loud_raise_when_a_dataset_is_empty(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.side_effect = [make_objectives("a"), []]
        config = DatasetAttackConfiguration(dataset_names=["d1", "d2"], auto_fetch=False)
        with pytest.raises(DatasetConstraintError, match="could not be loaded"):
            await config.get_attack_groups_by_dataset_async()


class TestBuildAttackGroups:
    """The ``_build_attack_groups`` override seam."""

    def test_default_groups_by_prompt_group_id(self) -> None:
        config = DatasetAttackConfiguration(dataset_names=["d1"])
        groups = config._build_attack_groups(make_objectives("a", "b"))
        assert len(groups) == 2
        assert all(isinstance(g, SeedAttackGroup) for g in groups)

    async def test_override_is_used(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.return_value = make_objectives("a", "b", "c")
        sentinel = [SeedAttackGroup(seeds=[SeedObjective(value="custom")])]

        class CustomConfig(DatasetAttackConfiguration):
            def _build_attack_groups(self, seeds):
                return sentinel

        config = CustomConfig(dataset_names=["d1"])
        assert await config.get_seed_attack_groups_async() == sentinel


class TestFetchDatasetAsync:
    """``_fetch_dataset_async`` provider interaction."""

    async def test_unregistered_name_does_not_fetch(self, mock_memory: MagicMock) -> None:
        config = DatasetConfiguration(dataset_names=["d1"])
        with patch(PROVIDER_PATCH_TARGET) as provider:
            provider.get_all_dataset_names_async = AsyncMock(return_value=["other"])
            provider.fetch_datasets_async = AsyncMock()
            await config._fetch_dataset_async(dataset_name="d1")
        provider.fetch_datasets_async.assert_not_called()
        mock_memory.add_seed_datasets_to_memory_async.assert_not_called()

    async def test_registered_name_fetches_and_adds(self, mock_memory: MagicMock) -> None:
        config = DatasetConfiguration(dataset_names=["d1"])
        datasets = [MagicMock()]
        with patch(PROVIDER_PATCH_TARGET) as provider:
            provider.get_all_dataset_names_async = AsyncMock(return_value=["d1"])
            provider.fetch_datasets_async = AsyncMock(return_value=datasets)
            await config._fetch_dataset_async(dataset_name="d1")
        provider.fetch_datasets_async.assert_awaited_once_with(dataset_names=["d1"])
        mock_memory.add_seed_datasets_to_memory_async.assert_awaited_once()

    async def test_enumeration_error_propagates(self, mock_memory: MagicMock) -> None:
        config = DatasetConfiguration(dataset_names=["d1"])
        with patch(PROVIDER_PATCH_TARGET) as provider:
            provider.get_all_dataset_names_async = AsyncMock(side_effect=RuntimeError("boom"))
            with pytest.raises(RuntimeError, match="boom"):
                await config._fetch_dataset_async(dataset_name="d1")
        mock_memory.add_seed_datasets_to_memory_async.assert_not_called()

    async def test_fetch_failure_chains_root_cause(self, mock_memory: MagicMock) -> None:
        config = DatasetAttackConfiguration(dataset_names=["d1"])
        with patch(PROVIDER_PATCH_TARGET) as provider:
            provider.get_all_dataset_names_async = AsyncMock(side_effect=RuntimeError("boom"))
            with pytest.raises(DatasetConstraintError, match="auto-fetch") as exc_info:
                await config.get_seed_attack_groups_async()
        assert isinstance(exc_info.value.__cause__, RuntimeError)


class TestValidators:
    """The standalone validator builders and base ``validate``."""

    def test_require_nonempty_raises_on_empty(self) -> None:
        with pytest.raises(DatasetConstraintError):
            require_nonempty()(resolved())

    def test_require_nonempty_passes(self) -> None:
        require_nonempty()(resolved(SeedObjective(value="a")))

    def test_require_min_size_raises_when_too_few(self) -> None:
        with pytest.raises(DatasetConstraintError):
            require_min_size(3)(resolved(SeedObjective(value="a")))

    def test_require_min_size_passes(self) -> None:
        require_min_size(1)(resolved(SeedObjective(value="a")))

    def test_require_harm_categories_raises_when_missing(self) -> None:
        with pytest.raises(DatasetConstraintError):
            require_harm_categories({"illegal"})(resolved(SeedObjective(value="a")))

    def test_require_harm_categories_passes(self) -> None:
        item = SeedObjective(value="a", harm_categories=["illegal"])
        require_harm_categories({"illegal"})(resolved(item))

    def test_require_seed_type_raises_on_wrong_type(self) -> None:
        with pytest.raises(DatasetConstraintError, match="SeedObjective"):
            require_seed_type(SeedObjective)(resolved(SeedPrompt(value="p")))

    def test_require_seed_type_passes(self) -> None:
        require_seed_type(SeedObjective)(resolved(SeedObjective(value="a")))

    def test_require_inline_seeds_raises_on_dataset_names(self) -> None:
        item = SeedObjective(value="a")
        with pytest.raises(DatasetConstraintError, match="inline"):
            require_inline_seeds()(resolved(item, source_kind=DatasetSourceKind.MEMORY))

    def test_require_inline_seeds_passes_for_inline(self) -> None:
        item = SeedObjective(value="a")
        require_inline_seeds()(resolved(item, source_kind=DatasetSourceKind.INLINE))

    def test_forbid_inline_seeds_raises_on_inline(self) -> None:
        item = SeedObjective(value="a")
        with pytest.raises(DatasetConstraintError, match="inline"):
            forbid_inline_seeds()(resolved(item, source_kind=DatasetSourceKind.INLINE))

    def test_forbid_inline_seeds_passes_for_dataset_names(self) -> None:
        item = SeedObjective(value="a")
        forbid_inline_seeds()(resolved(item, source_kind=DatasetSourceKind.MEMORY))

    def test_restrict_dataset_names_passes_when_subset(self) -> None:
        item = SeedObjective(value="a")
        restrict_dataset_names({"d1", "d2"})(resolved(item, dataset_names=("d1",)))

    def test_restrict_dataset_names_raises_on_disallowed(self) -> None:
        item = SeedObjective(value="a")
        with pytest.raises(DatasetConstraintError, match="not allowed"):
            restrict_dataset_names({"d1"})(resolved(item, dataset_names=("d1", "rogue")))

    def test_restrict_dataset_names_passes_for_inline(self) -> None:
        item = SeedObjective(value="a")
        restrict_dataset_names({"d1"})(resolved(item, source_kind=DatasetSourceKind.INLINE))

    def test_validate_raises_on_empty(self) -> None:
        config = DatasetConfiguration(dataset_names=["d1"])
        with pytest.raises(DatasetConstraintError, match="empty"):
            config.validate(resolved())


class TestSourceKind:
    """``source_kind`` reflects how the configuration was constructed."""

    def test_inline_seeds(self) -> None:
        config = DatasetConfiguration(seeds=make_objectives("a"))
        assert config.source_kind is DatasetSourceKind.INLINE

    def test_inline_seed_groups(self, sample_seed_groups: list[SeedGroup]) -> None:
        config = DatasetConfiguration(seed_groups=sample_seed_groups)
        assert config.source_kind is DatasetSourceKind.INLINE

    def test_dataset_names(self) -> None:
        config = DatasetConfiguration(dataset_names=["d1"])
        assert config.source_kind is DatasetSourceKind.MEMORY

    def test_unconfigured_is_memory(self) -> None:
        config = DatasetConfiguration()
        assert config.source_kind is DatasetSourceKind.MEMORY


class TestSourceValidatorsEndToEnd:
    """Source-kind validators wired through ``DatasetAttackConfiguration`` resolution."""

    async def test_require_inline_seeds_raises_for_dataset_names(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.return_value = make_objectives("a")
        config = DatasetAttackConfiguration(dataset_names=["d1"], validators=[require_inline_seeds()])
        with pytest.raises(DatasetConstraintError, match="inline"):
            await config.get_seed_attack_groups_async()

    async def test_require_inline_seeds_passes_for_inline(self) -> None:
        seeds = make_objectives("a", "b")
        config = DatasetAttackConfiguration(seeds=seeds, validators=[require_inline_seeds()])
        assert len(await config.get_seed_attack_groups_async()) == 2

    async def test_forbid_inline_seeds_raises_for_inline(self) -> None:
        config = DatasetAttackConfiguration(seeds=make_objectives("a"), validators=[forbid_inline_seeds()])
        with pytest.raises(DatasetConstraintError, match="inline"):
            await config.get_seed_attack_groups_async()


class TestResolvedDatasetNames:
    """``ResolvedDataset.dataset_names`` carries the contributing dataset names to validators."""

    async def test_resolution_exposes_contributing_names(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.side_effect = [make_objectives("a"), make_objectives("b")]
        seen: list[ResolvedDataset] = []
        config = DatasetAttackConfiguration(dataset_names=["d1", "d2"], validators=[seen.append])
        await config.get_seed_attack_groups_async()
        assert seen[0].dataset_names == ("d1", "d2")

    async def test_inline_reports_no_dataset_names(self) -> None:
        seen: list[ResolvedDataset] = []
        config = DatasetAttackConfiguration(seeds=make_objectives("a"), validators=[seen.append])
        await config.get_seed_attack_groups_async()
        assert seen[0].dataset_names == ()

    async def test_attack_groups_by_dataset_exposes_contributing_names(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.side_effect = [make_objectives("a"), make_objectives("b")]
        seen: list[ResolvedDataset] = []
        config = DatasetAttackConfiguration(dataset_names=["d1", "d2"], validators=[seen.append])
        await config.get_attack_groups_by_dataset_async()
        assert seen[0].dataset_names == ("d1", "d2")

    async def test_restrict_dataset_names_raises_for_rogue_dataset(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.return_value = make_objectives("a")
        config = DatasetAttackConfiguration(dataset_names=["rogue"], validators=[restrict_dataset_names({"d1", "d2"})])
        with pytest.raises(DatasetConstraintError, match="not allowed"):
            await config.get_seed_attack_groups_async()

    async def test_restrict_dataset_names_passes_for_allowed_dataset(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.return_value = make_objectives("a")
        config = DatasetAttackConfiguration(dataset_names=["d1"], validators=[restrict_dataset_names({"d1", "d2"})])
        groups = await config.get_seed_attack_groups_async()
        assert [g.objective.value for g in groups] == ["a"]


class TestCompoundDatasetAttackConfiguration:
    """``CompoundDatasetAttackConfiguration`` composes child configs with independent budgets."""

    def test_empty_configurations_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one child"):
            CompoundDatasetAttackConfiguration(configurations=[])

    def test_per_dataset_empty_names_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one dataset"):
            CompoundDatasetAttackConfiguration.per_dataset(dataset_names=[])

    def test_per_dataset_builds_one_child_per_name(self) -> None:
        config = CompoundDatasetAttackConfiguration.per_dataset(dataset_names=["d1", "d2"], max_dataset_size=4)
        assert len(config._configurations) == 2
        assert [child.dataset_names for child in config._configurations] == [["d1"], ["d2"]]
        assert all(child.max_dataset_size == 4 for child in config._configurations)

    def test_dataset_names_aggregates_and_dedups(self) -> None:
        config = CompoundDatasetAttackConfiguration(
            configurations=[
                DatasetAttackConfiguration(dataset_names=["d1"]),
                DatasetAttackConfiguration(dataset_names=["d1", "d2"]),
            ]
        )
        assert config.dataset_names == ["d1", "d2"]

    def test_source_kind_inline_when_all_children_inline(self) -> None:
        config = CompoundDatasetAttackConfiguration(
            configurations=[
                DatasetAttackConfiguration(seeds=make_objectives("a")),
                DatasetAttackConfiguration(seeds=make_objectives("b")),
            ]
        )
        assert config.source_kind is DatasetSourceKind.INLINE

    def test_source_kind_memory_when_any_child_from_memory(self) -> None:
        config = CompoundDatasetAttackConfiguration(
            configurations=[
                DatasetAttackConfiguration(seeds=make_objectives("a")),
                DatasetAttackConfiguration(dataset_names=["d1"]),
            ]
        )
        assert config.source_kind is DatasetSourceKind.MEMORY

    async def test_flat_concatenates_children_with_per_child_budget(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.side_effect = [make_objectives("a", "b", "c", "d"), make_objectives("e", "f", "g", "h")]
        config = CompoundDatasetAttackConfiguration.per_dataset(dataset_names=["d1", "d2"], max_dataset_size=3)
        groups = await config.get_seed_attack_groups_async()
        assert len(groups) == 6

    async def test_by_dataset_merges_children(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.side_effect = [make_objectives("a", "b", "c", "d"), make_objectives("e", "f", "g", "h")]
        config = CompoundDatasetAttackConfiguration.per_dataset(dataset_names=["d1", "d2"], max_dataset_size=3)
        result = await config.get_attack_groups_by_dataset_async()
        assert {name: len(groups) for name, groups in result.items()} == {"d1": 3, "d2": 3}

    async def test_compound_max_caps_combined_result(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.side_effect = [make_objectives("a", "b", "c"), make_objectives("d", "e", "f")]
        config = CompoundDatasetAttackConfiguration(
            configurations=[
                DatasetAttackConfiguration(dataset_names=["d1"]),
                DatasetAttackConfiguration(dataset_names=["d2"]),
            ],
            max_dataset_size=2,
        )
        groups = await config.get_seed_attack_groups_async()
        assert len(groups) == 2

    async def test_inline_children_combine(self) -> None:
        config = CompoundDatasetAttackConfiguration(
            configurations=[
                DatasetAttackConfiguration(seeds=make_objectives("a")),
                DatasetAttackConfiguration(seeds=make_objectives("b")),
            ]
        )
        groups = await config.get_seed_attack_groups_async()
        assert sorted(g.objective.value for g in groups) == ["a", "b"]


class TestDatasetConfigurationFilters:
    """Filters are threaded into ``get_seeds`` and applied before sampling."""

    async def test_filters_passed_to_get_seeds(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.return_value = make_objectives("a", "b")
        config = DatasetAttackConfiguration(dataset_names=["d1"], filters={"harm_categories": ["cyber"]})
        await config.get_seed_attack_groups_async()
        mock_memory.get_seeds.assert_called_with(dataset_name="d1", harm_categories=["cyber"])

    async def test_filter_removing_all_seeds_raises_specific_error(self, mock_memory: MagicMock) -> None:
        def _get_seeds(*, dataset_name, **filters):
            return [] if filters else make_objectives("a", "b")

        mock_memory.get_seeds.side_effect = _get_seeds
        config = DatasetAttackConfiguration(
            dataset_names=["d1"], filters={"harm_categories": ["missing"]}, auto_fetch=False
        )
        with pytest.raises(DatasetConstraintError, match="none match the configured filters"):
            await config.get_seed_attack_groups_async()

    async def test_update_filters_merges(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.return_value = make_objectives("a")
        config = DatasetAttackConfiguration(dataset_names=["d1"], filters={"harm_categories": ["a"]})
        config.update_filters(filters={"authors": ["jones"]})
        await config.get_seed_attack_groups_async()
        mock_memory.get_seeds.assert_called_with(dataset_name="d1", harm_categories=["a"], authors=["jones"])

    def test_filters_property_returns_copy(self) -> None:
        config = DatasetAttackConfiguration(dataset_names=["d1"], filters={"harm_categories": ["a"]})
        config.filters["authors"] = ["mutated"]
        assert config.filters == {"harm_categories": ["a"]}

    async def test_per_dataset_threads_filters_to_children(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.return_value = make_objectives("a")
        config = CompoundDatasetAttackConfiguration.per_dataset(
            dataset_names=["d1"], filters={"harm_categories": ["cyber"]}
        )
        await config.get_seed_attack_groups_async()
        mock_memory.get_seeds.assert_called_with(dataset_name="d1", harm_categories=["cyber"])

    async def test_compound_update_filters_propagates_to_children(self, mock_memory: MagicMock) -> None:
        mock_memory.get_seeds.return_value = make_objectives("a")
        config = CompoundDatasetAttackConfiguration.per_dataset(dataset_names=["d1"])
        config.update_filters(filters={"harm_categories": ["cyber"]})
        await config.get_seed_attack_groups_async()
        mock_memory.get_seeds.assert_called_with(dataset_name="d1", harm_categories=["cyber"])
