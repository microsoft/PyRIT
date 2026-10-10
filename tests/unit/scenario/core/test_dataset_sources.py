# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Named selection, explicit preparation, and durable storage contracts."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event

from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory, MemoryInterface, SQLiteMemory
from pyrit.memory.memory_models import SeedEntry
from pyrit.models import AllAvailableDatasetSize, BoundedDatasetSize, SeedDataset, SeedObjective, SeedOrigin
from pyrit.models.dataset_limit import DatasetLimit
from pyrit.scenario import (
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    DatasetConfiguration,
    DatasetFetchPolicy,
    DatasetSource,
)
from pyrit.scenario.core.dataset_configuration import DatasetConstraintError, require_min_size


def dataset(name: str, count: int = 12) -> SeedDataset:
    """Build an in-memory provider result."""
    return SeedDataset(
        dataset_name=name,
        seeds=[SeedObjective(value=f"{name}-{index}", dataset_name=name) for index in range(count)],
    )


@pytest.fixture
def memory() -> MagicMock:
    """Memory with three complete fixture populations."""
    populations = {name: dataset(name).seeds for name in ("a", "b", "c")}
    instance = MagicMock(spec=MemoryInterface)
    instance.get_seed_dataset_names_async = AsyncMock(return_value=list(populations))
    instance.get_seeds_async = AsyncMock(
        side_effect=lambda *, dataset_name, **kwargs: populations.get(dataset_name, [])
    )
    instance.add_seed_datasets_to_memory_async = AsyncMock()
    return instance


@pytest.mark.parametrize("total, expected", [(None, 15), (5, 5), (30, 15)])
async def test_source_and_total_limits(*, memory: MagicMock, total: int | None, expected: int) -> None:
    config = DatasetAttackConfiguration(sources=[DatasetSource(name=name) for name in ("a", "b", "c")], max_total=total)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        groups = await config.get_attack_groups_by_dataset_async()
        assert sum(map(len, groups.values())) == expected
        assert all(len(population) <= 5 for population in groups.values())
        assert len(await config.get_attack_seed_groups_async()) == expected
        assert len(await config.get_attack_seed_groups_async(apply_sampling=False)) == 36
    assert config.get_size_budget() == BoundedDatasetSize(value=expected)


async def test_explicit_source_all_is_not_inherited(memory: MagicMock) -> None:
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="a"), DatasetSource(name="b", max_size="all")])
    assert config.get_size_budget() == AllAvailableDatasetSize()
    assert config.with_overrides(max_total=9).get_size_budget() == BoundedDatasetSize(value=9)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        groups = await config.get_attack_groups_by_dataset_async()
    assert {name: len(items) for name, items in groups.items()} == {"a": 5, "b": 12}


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_source_rejects_invalid_limits(limit: int) -> None:
    with pytest.raises(DatasetConstraintError, match="positive integer"):
        DatasetSource(name="a", max_size=limit)
    with pytest.raises(DatasetConstraintError, match="positive integer"):
        DatasetAttackConfiguration(max_total=limit)


def test_source_names_and_alias_conflicts() -> None:
    with pytest.raises(DatasetConstraintError, match="non-empty"):
        DatasetSource(name=" ")
    with pytest.raises(DatasetConstraintError, match="Duplicate"):
        DatasetAttackConfiguration(sources=[DatasetSource(name="a"), DatasetSource(name="a")])
    with pytest.raises(ValueError, match="Only one"):
        DatasetAttackConfiguration(sources=[DatasetSource(name="a")], dataset_names=["a"])
    with pytest.raises(ValueError, match="only one"):
        DatasetAttackConfiguration(max_total=3, max_dataset_size=4)


@pytest.mark.parametrize("limit", [None, "", "default", "all", 3])
def test_duplicate_total_arguments_raise_even_when_equal(limit: DatasetLimit) -> None:
    with pytest.raises(ValueError, match="only one.*max_dataset_size.*max_total"):
        DatasetAttackConfiguration(max_total=limit, max_dataset_size=limit)


@pytest.mark.parametrize("policy", list(DatasetFetchPolicy))
def test_duplicate_fetch_arguments_raise_even_when_equal(policy: DatasetFetchPolicy) -> None:
    with pytest.raises(ValueError, match="only one.*auto_fetch.*fetch"):
        DatasetAttackConfiguration(fetch=policy, auto_fetch=policy is DatasetFetchPolicy.IF_MISSING)


@pytest.mark.parametrize("compound", [False, True])
async def test_preparation_checks_names_without_reading_seeds_async(*, memory: MagicMock, compound: bool) -> None:
    sources = [DatasetSource(name="a"), DatasetSource(name="b", fetch=DatasetFetchPolicy.NEVER)]
    config = (
        CompoundDatasetAttackConfiguration(
            configurations=[DatasetAttackConfiguration(sources=[source]) for source in sources]
        )
        if compound
        else DatasetAttackConfiguration(sources=sources)
    )
    memory.get_seeds_async.side_effect = AssertionError("Preparation must not load seed contents")
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async") as lookup,
    ):
        await config.prepare_async()
    memory.get_seed_dataset_names_async.assert_awaited_once()
    memory.get_seeds_async.assert_not_awaited()
    lookup.assert_not_awaited()
    memory.add_seed_datasets_to_memory_async.assert_not_awaited()


@pytest.mark.parametrize("total", [None, "", "default", "all", 3, 9])
@pytest.mark.parametrize("source_limit, population", [("all", 12), (None, 5), ("default", 5), (5, 5)])
async def test_legacy_total_has_canonical_defaults_async(
    *, memory: MagicMock, total: DatasetLimit, source_limit: DatasetLimit, population: int
) -> None:
    with pytest.warns(DeprecationWarning):
        legacy = DatasetAttackConfiguration(
            sources=[DatasetSource(name="a")], max_dataset_size=total, max_per_dataset=source_limit
        )
    canonical = DatasetAttackConfiguration(
        sources=[DatasetSource(name="a")], max_total=total, max_per_dataset=source_limit
    )
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        for config in (legacy, canonical):
            assert len(await config.get_attack_seed_groups_async()) == min(
                population, total if isinstance(total, int) else population
            )
    assert legacy.get_size_budget() == canonical.get_size_budget()


def test_deprecated_total_preserves_default_source_cap() -> None:
    with pytest.warns(DeprecationWarning):
        legacy = DatasetAttackConfiguration(sources=[DatasetSource(name="a")], max_dataset_size=10)
    canonical = DatasetAttackConfiguration(sources=[DatasetSource(name="a")], max_total=10)
    assert legacy.max_per_dataset == canonical.max_per_dataset == 5
    assert legacy.get_size_budget() == canonical.get_size_budget() == BoundedDatasetSize(value=5)


@pytest.mark.parametrize(("total", "expected"), [(None, 5), ("", 5), ("default", 5), ("all", 8), (3, 3), (9, 8)])
async def test_inline_total_alias_parity_async(*, total: DatasetLimit, expected: int) -> None:
    seeds = [SeedObjective(value=str(index)) for index in range(8)]
    with pytest.warns(DeprecationWarning):
        legacy = DatasetAttackConfiguration(seeds=seeds, max_dataset_size=total)
    canonical = DatasetAttackConfiguration(seeds=seeds, max_total=total)
    assert legacy.max_per_dataset == canonical.max_per_dataset == "all"
    for config in (legacy, canonical):
        assert len(await config.get_attack_seed_groups_async()) == expected


@pytest.mark.parametrize("configuration_class", [DatasetConfiguration, DatasetAttackConfiguration])
def test_inline_source_caps_raise_on_construction_and_override(
    configuration_class: type[DatasetConfiguration],
) -> None:
    seeds = [SeedObjective(value="inline")]
    with pytest.raises(DatasetConstraintError, match="Inline.*max_total"):
        configuration_class(seeds=seeds, max_per_dataset=2)
    config = configuration_class(seeds=seeds, max_per_dataset=None)
    with pytest.raises(DatasetConstraintError, match="Inline.*max_total"):
        config.with_overrides(max_per_dataset=2)
    assert config.with_overrides(max_total=None).max_total == config.max_total
    assert config.with_overrides(max_total="all").max_total == "all"


@pytest.mark.parametrize(("per_dataset", "expected"), [("default", 10), ("all", 17)])
async def test_explicit_source_limits_control_selection_async(
    *, memory: MagicMock, per_dataset: DatasetLimit, expected: int
) -> None:
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="a"), DatasetSource(name="b")], max_per_dataset=per_dataset, max_total=17
    )
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        assert len(await config.get_attack_seed_groups_async()) == expected
    assert config.get_size_budget() == BoundedDatasetSize(value=expected)
    changed = config.with_overrides(max_total=3)
    assert changed.max_per_dataset == config.max_per_dataset
    assert config.max_total == 17
    assert changed.get_size_budget() == BoundedDatasetSize(value=3)


@pytest.mark.parametrize("compound", [False, True])
async def test_total_sampling_keeps_empty_source_keys_async(*, memory: MagicMock, compound: bool) -> None:
    sources = [DatasetSource(name="a"), DatasetSource(name="b")]
    config = (
        CompoundDatasetAttackConfiguration(
            configurations=[DatasetAttackConfiguration(sources=[source]) for source in sources],
            max_total=1,
        )
        if compound
        else DatasetAttackConfiguration(sources=sources, max_total=1)
    )
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        groups = await config.get_attack_groups_by_dataset_async()
    assert list(groups) == ["a", "b"]
    assert sorted(map(len, groups.values())) == [0, 1]


async def test_all_empty_groups_raise_with_retained_keys_async(memory: MagicMock) -> None:
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="a")])
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        patch.object(config, "_build_attack_groups", return_value=[]),
        pytest.raises(DatasetConstraintError, match="attack-group dataset is empty"),
    ):
        await config.get_attack_groups_by_dataset_async()


@pytest.mark.parametrize("default", [None, "", "default"])
async def test_default_limits_match_omission_async(*, memory: MagicMock, default: DatasetLimit) -> None:
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="a", max_size=default), DatasetSource(name="b")],
        max_per_dataset=default,
        max_total=default,
    )
    assert config.max_per_dataset == 5
    assert config.max_total == "all"
    assert config.sources[0].max_size == "default"
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        groups = await config.get_attack_groups_by_dataset_async()
    assert {name: len(population) for name, population in groups.items()} == {"a": 5, "b": 5}
    inline = DatasetAttackConfiguration(seeds=dataset("inline").seeds, max_total=default)
    assert len(await inline.get_attack_seed_groups_async()) == 5


@pytest.mark.parametrize("default", [None, "", "default"])
async def test_default_overrides_preserve_existing_caps_async(*, memory: MagicMock, default: DatasetLimit) -> None:
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="a", max_size=default)], max_per_dataset=4, max_total=3
    )
    copied = config.with_overrides(max_per_dataset=default, max_total=default)
    assert copied.max_per_dataset == 4
    assert copied.max_total == 3
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        assert len(await copied.get_attack_seed_groups_async()) == 3
        assert len(await copied.with_overrides(max_total="all").get_attack_seed_groups_async()) == 4
        unlimited = copied.with_overrides(max_per_dataset="all", max_total="all")
        assert len(await unlimited.get_attack_seed_groups_async()) == 12
    assert unlimited.max_per_dataset == unlimited.max_total == "all"


@pytest.mark.parametrize("default", [None, "", "default"])
def test_limit_setters_use_constructor_defaults(default: DatasetLimit) -> None:
    config = DatasetAttackConfiguration(seeds=dataset("inline").seeds, max_total=2)
    config.max_total = default
    assert config.max_total == 5
    with pytest.warns(DeprecationWarning):
        config.max_dataset_size = "all"
    assert config.max_total == "all"
    with pytest.warns(DeprecationWarning):
        config.max_dataset_size = default
    assert config.max_total == 5
    named = DatasetAttackConfiguration(sources=[DatasetSource(name="a")], max_per_dataset="all")
    named.max_per_dataset = default
    assert named.max_per_dataset == 5


@pytest.mark.parametrize("default", [None, "", "default"])
async def test_compound_default_and_all_preserve_child_caps_async(default: DatasetLimit) -> None:
    child = DatasetAttackConfiguration(seeds=dataset("inline").seeds, max_total=3)
    compound = CompoundDatasetAttackConfiguration(configurations=[child], max_total=default)
    assert compound.max_total == "all"
    assert len(await compound.get_attack_seed_groups_async()) == 3
    capped = compound.with_overrides(max_total=2)
    assert len(await capped.with_overrides(max_total=default).get_attack_seed_groups_async()) == 2
    assert len(await capped.with_overrides(max_total="all").get_attack_seed_groups_async()) == 3


def test_compound_source_override_rejects_inline_child_cap() -> None:
    config = CompoundDatasetAttackConfiguration(
        configurations=[DatasetAttackConfiguration(seeds=[SeedObjective(value="inline")])]
    )
    with pytest.raises(DatasetConstraintError, match="Inline.*max_total"):
        config.with_overrides(max_per_dataset=2)


def test_overrides_keep_class_and_do_not_mutate_containers() -> None:
    class CustomConfiguration(DatasetAttackConfiguration):
        pass

    original = CustomConfiguration(sources=[DatasetSource(name="a")], filters={"harm_categories": ["first"]})
    changed = original.with_overrides(max_total=2, filters={"harm_categories": ["second"]})
    assert type(changed) is CustomConfiguration
    assert changed.sources == original.sources
    assert original.max_total == "all"
    assert original.filters == {"harm_categories": ["first"]}
    compound = CompoundDatasetAttackConfiguration(configurations=[original])
    copied = compound.with_overrides(fetch=DatasetFetchPolicy.NEVER, filters={"harm_categories": ["second"]})
    assert copied._configurations[0] is not original
    assert original.fetch is DatasetFetchPolicy.IF_MISSING
    assert copied._configurations[0].fetch is DatasetFetchPolicy.NEVER
    assert original.filters == {"harm_categories": ["first"]}


async def test_compound_validates_full_population_before_sampling(memory: MagicMock) -> None:
    config = CompoundDatasetAttackConfiguration(
        configurations=[DatasetAttackConfiguration(sources=[DatasetSource(name="a")], max_total=1)],
        validators=[require_min_size(12)],
    )
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        assert len(await config.get_attack_seed_groups_async()) == 1


@pytest.mark.parametrize("compound", [False, True])
@pytest.mark.parametrize("policy", [DatasetFetchPolicy.NEVER, DatasetFetchPolicy.IF_MISSING])
async def test_preflight_checks_last_missing_source_before_any_fetch(
    *, memory: MagicMock, compound: bool, policy: DatasetFetchPolicy
) -> None:
    first = DatasetSource(name="registered")
    last = DatasetSource(name="memory_only", fetch=policy)
    config = (
        CompoundDatasetAttackConfiguration(
            configurations=[DatasetAttackConfiguration(sources=[source]) for source in (first, last)]
        )
        if compound
        else DatasetAttackConfiguration(sources=[first, last])
    )
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.fetch_dataset_async = AsyncMock(return_value=dataset("registered"))
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"registered": provider}),
        pytest.raises(DatasetConstraintError, match="Import"),
    ):
        await config.prepare_async()
    provider.fetch_dataset_async.assert_not_awaited()
    memory.add_seed_datasets_to_memory_async.assert_not_awaited()


async def test_filter_miss_never_fetches(memory: MagicMock) -> None:
    memory.get_seeds_async.side_effect = lambda **kwargs: [] if "harm_categories" in kwargs else dataset("a").seeds
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="a")], filters={"harm_categories": ["absent"]})
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async") as lookup,
    ):
        await config.prepare_async()
        with pytest.raises(DatasetConstraintError, match="none match"):
            await config.get_attack_seed_groups_async()
    lookup.assert_not_awaited()
    memory.add_seed_datasets_to_memory_async.assert_not_awaited()


async def test_provider_failure_is_not_cached_or_persisted(memory: MagicMock) -> None:
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="fresh")])
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.fetch_dataset_async.side_effect = RuntimeError("provider failed")
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"fresh": provider}),
    ):
        for _ in range(2):
            with pytest.raises(RuntimeError, match="provider failed"):
                await config.prepare_async()
    assert provider.fetch_dataset_async.await_count == 2
    memory.add_seed_datasets_to_memory_async.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("origin", list(SeedOrigin))
async def test_memory_only_reuse_and_new_run_rechecks_presence(
    *, sqlite_instance: SQLiteMemory, origin: SeedOrigin
) -> None:
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="stored")])
    seeds = dataset("stored").seeds
    for seed in seeds:
        seed.origin = origin
    await sqlite_instance.add_seeds_to_memory_async(seeds=seeds, added_by="test")
    with patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={}) as lookup:
        await config.prepare_async()
        assert len(await config.get_attack_seed_groups_async()) == 5
        lookup.assert_not_awaited()
        await sqlite_instance.remove_seeds_from_memory_async(dataset_name="stored")
        with pytest.raises(DatasetConstraintError, match="no provider"):
            await config.prepare_async()
        lookup.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
async def test_preparation_persists_then_reuses(sqlite_instance: SQLiteMemory) -> None:
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="fresh", max_size=2)])
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.fetch_dataset_async = AsyncMock(return_value=dataset("fresh"))
    with patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"fresh": provider}):
        await config.prepare_async()
        assert len(await config.get_attack_seed_groups_async()) == 2
        assert len(await sqlite_instance.get_seeds_async(dataset_name="fresh")) == 12
        await config.prepare_async()
    provider.fetch_dataset_async.assert_awaited_once()


@pytest.mark.usefixtures("patch_central_database")
async def test_insert_failure_rolls_back_complete_dataset(sqlite_instance: SQLiteMemory) -> None:
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="fresh")])
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.fetch_dataset_async = AsyncMock(return_value=dataset("fresh", count=2))
    inserted = 0

    def fail_second_insert(mapper: object, connection: object, target: SeedEntry) -> None:
        nonlocal inserted
        inserted += 1
        if inserted == 2:
            raise RuntimeError("injected insert failure")

    event.listen(SeedEntry, "before_insert", fail_second_insert)
    try:
        with (
            patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"fresh": provider}),
            pytest.raises(RuntimeError, match="injected insert failure"),
        ):
            await config.prepare_async()
    finally:
        event.remove(SeedEntry, "before_insert", fail_second_insert)
    assert await sqlite_instance.get_seeds_async(dataset_name="fresh") == []


@pytest.mark.parametrize("name, seeds", [("wrong", [SeedObjective(value="x", dataset_name="wrong")]), ("fresh", [])])
async def test_invalid_provider_result_never_persists(
    *, memory: MagicMock, name: str, seeds: list[SeedObjective]
) -> None:
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="fresh")])
    provider = MagicMock(spec=SeedDatasetProvider)
    result = dataset(name)
    result.seeds = seeds
    provider.fetch_dataset_async = AsyncMock(return_value=result)
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"fresh": provider}),
        pytest.raises(DatasetConstraintError, match="non-empty seeds"),
    ):
        await config.prepare_async()
    memory.add_seed_datasets_to_memory_async.assert_not_awaited()
