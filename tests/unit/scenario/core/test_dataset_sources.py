# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Named selection, explicit preparation, and durable storage contracts."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import aiofiles
import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from unit.mocks import MockPromptTarget

from pyrit.datasets import SeedDatasetProvider
from pyrit.executor.attack import PromptSendingAttack
from pyrit.memory import CentralMemory, MemoryInterface, SQLiteMemory
from pyrit.memory.memory_interface import PreparedSeedBatch
from pyrit.memory.memory_models import SeedEntry
from pyrit.models import (
    AllAvailableDatasetSize,
    AttackSeedGroup,
    BoundedDatasetSize,
    Seed,
    SeedDataset,
    SeedObjective,
    SeedOrigin,
    SeedPrompt,
)
from pyrit.models.dataset_limit import DatasetLimit
from pyrit.scenario import (
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    DatasetConfiguration,
    DatasetFetchPolicy,
    DatasetSource,
    Scenario,
)
from pyrit.scenario.core import AtomicAttack, AttackTechnique, BaselineAttackPolicy, ScenarioTechnique
from pyrit.scenario.core.dataset_configuration import (
    DatasetConstraintError,
    ResolvedDataset,
    require_min_size,
    require_seed_type,
)
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.score import SubStringScorer
from test_support.fixtures import use_sqlite_memory


def dataset(name: str, count: int = 12) -> SeedDataset:
    """Build an in-memory provider result."""
    return SeedDataset(
        dataset_name=name,
        seeds=[SeedObjective(value=f"{name}-{index}", dataset_name=name) for index in range(count)],
    )


def _copy_memory_to_file(*, source: SQLiteMemory, path: Path) -> SQLiteMemory:
    with closing(source.engine.raw_connection()) as connection, closing(sqlite3.connect(path)) as destination:
        driver = connection.driver_connection
        if not isinstance(driver, sqlite3.Connection):
            raise TypeError("Expected a SQLite connection.")
        driver.backup(destination)
    memory = SQLiteMemory.__new__(SQLiteMemory)
    with patch.object(memory, "cleanup"):
        memory.__init__(db_path=path, skip_schema_migration=True)
    memory.results_path = source.results_path
    return memory


@pytest.fixture
async def file_memory(*, sqlite_instance: SQLiteMemory, tmp_path: Path) -> AsyncIterator[SQLiteMemory]:
    memory = await asyncio.to_thread(_copy_memory_to_file, source=sqlite_instance, path=tmp_path / "refresh.db")
    try:
        with use_sqlite_memory(memory):
            yield memory
    finally:
        await memory.dispose_engine_async()


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
    instance.prepare_seed_batch_async = AsyncMock(
        side_effect=lambda *, seeds, added_by: PreparedSeedBatch(
            _seeds=tuple(seed.model_copy(deep=True) for seed in seeds)
        )
    )
    return instance


@pytest.fixture(params=[False, True], ids=["registered", "configured"])
def configured_provider(request: pytest.FixtureRequest) -> bool:
    value = request.param
    assert isinstance(value, bool)
    return value


@pytest.mark.parametrize("provider", [object(), SeedDatasetProvider])
def test_source_rejects_invalid_provider(provider: object) -> None:
    with pytest.raises(DatasetConstraintError, match="SeedDatasetProvider instance"):
        DatasetSource(name="fresh", provider=provider)  # type: ignore[arg-type]


def test_source_rejects_provider_name_mismatch() -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "different"
    with pytest.raises(DatasetConstraintError, match="match source 'fresh'"):
        DatasetSource(name="fresh", provider=provider)
    provider.fetch_dataset_async.assert_not_awaited()


async def test_preparation_revalidates_changed_provider_name(memory: MagicMock) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "fresh"
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="fresh", provider=provider)])
    provider.dataset_name = "different"
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        pytest.raises(DatasetConstraintError, match="match source 'fresh'"),
    ):
        await config.prepare_async()
    memory.get_seed_dataset_names_async.assert_not_awaited()
    provider.fetch_dataset_async.assert_not_awaited()


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

    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "a"
    original = CustomConfiguration(
        sources=[DatasetSource(name="a", provider=provider)], filters={"harm_categories": ["first"]}
    )
    changed = original.with_overrides(max_total=2, filters={"harm_categories": ["second"]})
    assert type(changed) is CustomConfiguration
    assert changed.sources == original.sources
    assert changed.sources[0].provider is provider
    assert original.max_total == "all"
    assert original.filters == {"harm_categories": ["first"]}
    compound = CompoundDatasetAttackConfiguration(configurations=[original])
    copied = compound.with_overrides(fetch=DatasetFetchPolicy.NEVER, filters={"harm_categories": ["second"]})
    assert copied._configurations[0] is not original
    assert copied._configurations[0].sources[0].provider is provider
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
    *, memory: MagicMock, compound: bool, policy: DatasetFetchPolicy, configured_provider: bool
) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "registered"
    provider.fetch_dataset_async = AsyncMock(return_value=dataset("registered"))
    first = DatasetSource(name="registered", provider=provider if configured_provider else None)
    last = DatasetSource(name="memory_only", fetch=policy)
    config = (
        CompoundDatasetAttackConfiguration(
            configurations=[DatasetAttackConfiguration(sources=[source]) for source in (first, last)]
        )
        if compound
        else DatasetAttackConfiguration(sources=[first, last])
    )
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"registered": provider}),
        pytest.raises(DatasetConstraintError, match="Import"),
    ):
        await config.prepare_async()
    provider.fetch_dataset_async.assert_not_awaited()
    memory.add_seed_datasets_to_memory_async.assert_not_awaited()


async def test_filter_miss_never_fetches(*, memory: MagicMock, configured_provider: bool) -> None:
    memory.get_seeds_async.side_effect = lambda **kwargs: [] if "harm_categories" in kwargs else dataset("a").seeds
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "a"
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="a", provider=provider if configured_provider else None)],
        filters={"harm_categories": ["absent"]},
    )
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async") as lookup,
    ):
        await config.prepare_async()
        with pytest.raises(DatasetConstraintError, match="none match"):
            await config.get_attack_seed_groups_async()
    lookup.assert_not_awaited()
    provider.fetch_dataset_async.assert_not_awaited()
    memory.add_seed_datasets_to_memory_async.assert_not_awaited()


async def test_provider_failure_is_not_cached_or_persisted(*, memory: MagicMock, configured_provider: bool) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "fresh"
    provider.fetch_dataset_async.side_effect = RuntimeError("provider failed")
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="fresh", provider=provider if configured_provider else None)]
    )
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
@pytest.mark.parametrize("policy", [DatasetFetchPolicy.NEVER, DatasetFetchPolicy.IF_MISSING])
async def test_memory_only_reuse_and_new_run_rechecks_presence(
    *, sqlite_instance: SQLiteMemory, origin: SeedOrigin, policy: DatasetFetchPolicy, configured_provider: bool
) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "stored"
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="stored", provider=provider if configured_provider else None)], fetch=policy
    )
    seeds = dataset("stored").seeds
    for seed in seeds:
        seed.origin = origin
    await sqlite_instance.add_seeds_to_memory_async(seeds=seeds, added_by="test")
    with patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={}) as lookup:
        await config.prepare_async()
        assert len(await config.get_attack_seed_groups_async()) == 5
        lookup.assert_not_awaited()
        provider.fetch_dataset_async.assert_not_awaited()
        await sqlite_instance.remove_seeds_from_memory_async(dataset_name="stored")
        if configured_provider and policy is DatasetFetchPolicy.IF_MISSING:
            provider.fetch_dataset_async.return_value = dataset("stored")
            await config.prepare_async()
            provider.fetch_dataset_async.assert_awaited_once()
            lookup.assert_not_awaited()
        else:
            with pytest.raises(
                DatasetConstraintError, match="never" if policy is DatasetFetchPolicy.NEVER else "no provider"
            ):
                await config.prepare_async()
            provider.fetch_dataset_async.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("limit", "selected"), [("default", 5), (2, 2), (10, 10)])
async def test_preparation_persists_then_reuses(
    *, sqlite_instance: SQLiteMemory, configured_provider: bool, limit: DatasetLimit, selected: int
) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "fresh"
    provider.fetch_dataset_async = AsyncMock(return_value=dataset("fresh"))
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="fresh", max_size=limit, provider=provider if configured_provider else None)]
    )
    with patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"fresh": provider}) as lookup:
        await config.prepare_async()
        assert len(await config.get_attack_seed_groups_async()) == selected
        assert len(await sqlite_instance.get_seeds_async(dataset_name="fresh")) == 12
        await config.prepare_async()
        provider.fetch_dataset_async.assert_awaited_once()
        if configured_provider:
            lookup.assert_not_awaited()
        else:
            lookup.assert_awaited_once()
        await sqlite_instance.remove_seeds_from_memory_async(dataset_name="fresh")
        await config.prepare_async()
        assert len(await sqlite_instance.get_seeds_async(dataset_name="fresh")) == 12
    assert provider.fetch_dataset_async.await_count == 2


@pytest.mark.usefixtures("patch_central_database")
async def test_insert_failure_rolls_back_complete_dataset(
    *, sqlite_instance: SQLiteMemory, configured_provider: bool
) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "fresh"
    provider.fetch_dataset_async = AsyncMock(return_value=dataset("fresh", count=2))
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="fresh", provider=provider if configured_provider else None)]
    )
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


@pytest.mark.parametrize(
    "name, seeds",
    [
        ("wrong", [SeedObjective(value="x", dataset_name="wrong")]),
        ("fresh", []),
        ("fresh", [SeedObjective(value="x", dataset_name="wrong")]),
    ],
)
async def test_invalid_provider_result_never_persists(
    *, memory: MagicMock, name: str, seeds: list[SeedObjective], configured_provider: bool
) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "fresh"
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="fresh", provider=provider if configured_provider else None)]
    )
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


async def test_preparation_resolves_only_unconfigured_missing_providers(memory: MagicMock) -> None:
    configured = MagicMock(spec=SeedDatasetProvider)
    configured.dataset_name = "fresh"
    configured.fetch_dataset_async.return_value = dataset("fresh")
    registered = MagicMock(spec=SeedDatasetProvider)
    registered.fetch_dataset_async.return_value = dataset("remote")
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="fresh", provider=configured), DatasetSource(name="remote")]
    )
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"remote": registered}) as lookup,
    ):
        await config.prepare_async()
    assert lookup.call_args.kwargs["dataset_names"] == ["remote"]
    configured.fetch_dataset_async.assert_awaited_once()
    registered.fetch_dataset_async.assert_awaited_once()
    assert memory.add_seed_datasets_to_memory_async.await_count == 2


@pytest.mark.parametrize("policy", [DatasetFetchPolicy.IF_MISSING, DatasetFetchPolicy.ALWAYS])
async def test_configured_provider_takes_precedence(*, memory: MagicMock, policy: DatasetFetchPolicy) -> None:
    configured = MagicMock(spec=SeedDatasetProvider)
    configured.dataset_name = "fresh"
    configured.fetch_dataset_async.return_value = dataset("fresh")
    registered = MagicMock(spec=SeedDatasetProvider)
    config = DatasetAttackConfiguration(sources=[DatasetSource(name="fresh", provider=configured)], fetch=policy)
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"fresh": registered}) as lookup,
    ):
        await config.prepare_async()
    lookup.assert_not_awaited()
    configured.fetch_dataset_async.assert_awaited_once()
    registered.fetch_dataset_async.assert_not_awaited()


@pytest.mark.parametrize("provider_selection", ["same", "different", "none"])
async def test_compound_preparation_requires_one_provider_per_name(
    *, memory: MagicMock, provider_selection: str
) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "fresh"
    provider.fetch_dataset_async.return_value = dataset("fresh")
    second = MagicMock(spec=SeedDatasetProvider)
    second.dataset_name = "fresh"
    second_provider = {"same": provider, "different": second, "none": None}[provider_selection]
    config = CompoundDatasetAttackConfiguration(
        configurations=[
            DatasetAttackConfiguration(sources=[DatasetSource(name="fresh", provider=provider)]),
            DatasetAttackConfiguration(sources=[DatasetSource(name="fresh", provider=second_provider)]),
        ]
    )
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        if provider_selection == "same":
            await config.prepare_async()
            provider.fetch_dataset_async.assert_awaited_once()
            memory.add_seed_datasets_to_memory_async.assert_awaited_once()
        else:
            with pytest.raises(DatasetConstraintError, match="conflicting providers"):
                await config.prepare_async()
            provider.fetch_dataset_async.assert_not_awaited()
            memory.add_seed_datasets_to_memory_async.assert_not_awaited()
    second.fetch_dataset_async.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("stored", [False, True])
@pytest.mark.parametrize("compound", [False, True])
async def test_refresh_replaces_complete_dataset_and_repeats_on_new_preparation_async(
    *, sqlite_instance: SQLiteMemory, configured_provider: bool, stored: bool, compound: bool
) -> None:
    if stored:
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedObjective(value="Manual edit", dataset_name="fresh", origin=SeedOrigin.USER)], added_by="user"
        )
    await sqlite_instance.add_seeds_to_memory_async(seeds=dataset("other", 1).seeds, added_by="test")
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "fresh"
    fresh = dataset("fresh", 3)
    for seed in fresh.seeds:
        seed.origin = SeedOrigin.GENERATED
    provider.fetch_dataset_async.return_value = fresh
    child = DatasetAttackConfiguration(
        sources=[DatasetSource(name="fresh", provider=provider if configured_provider else None)],
        fetch=DatasetFetchPolicy.ALWAYS,
        max_total=1,
    )
    config = CompoundDatasetAttackConfiguration(configurations=[child, child]) if compound else child
    with patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"fresh": provider}) as lookup:
        await config.prepare_async()
        stored_seeds = await sqlite_instance.get_seeds_async(dataset_name="fresh")
        assert {seed.value for seed in stored_seeds} == {"fresh-0", "fresh-1", "fresh-2"}
        assert all(
            seed.origin is SeedOrigin.GENERATED and seed.added_by == "DatasetConfiguration" for seed in stored_seeds
        )
        for _ in range(2):
            await config.get_attack_seed_groups_async()
        provider.fetch_dataset_async.assert_awaited_once()
        assert provider.fetch_dataset_async.await_args.kwargs == {"cache": False}
        assert lookup.await_count == (0 if configured_provider else 1)
        provider.fetch_dataset_async.return_value = dataset("fresh", 2)
        await config.prepare_async()
    assert provider.fetch_dataset_async.await_count == 2
    assert len(await sqlite_instance.get_seeds_async(dataset_name="fresh")) == 2
    assert len(await sqlite_instance.get_seeds_async(dataset_name="other")) == 1


@pytest.mark.parametrize("compound", [False, True])
async def test_refresh_preflight_rejects_stored_memory_only_source_before_fetch_async(
    *, memory: MagicMock, compound: bool
) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "a"
    sources = [DatasetSource(name="a", provider=provider), DatasetSource(name="b")]
    config = (
        CompoundDatasetAttackConfiguration(
            configurations=[
                DatasetAttackConfiguration(sources=[source], fetch=DatasetFetchPolicy.ALWAYS) for source in sources
            ]
        )
        if compound
        else DatasetAttackConfiguration(sources=sources, fetch=DatasetFetchPolicy.ALWAYS)
    )
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={}),
        pytest.raises(DatasetConstraintError, match="no provider to refresh"),
    ):
        await config.prepare_async()
    provider.fetch_dataset_async.assert_not_awaited()
    memory.replace_seeds_for_dataset_async.assert_not_awaited()


@pytest.mark.parametrize("policy", [DatasetFetchPolicy.NEVER, DatasetFetchPolicy.IF_MISSING])
async def test_refresh_compound_rejects_conflicting_policies_before_fetch_async(
    *, memory: MagicMock, policy: DatasetFetchPolicy
) -> None:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "a"
    config = CompoundDatasetAttackConfiguration(
        configurations=[
            DatasetAttackConfiguration(sources=[DatasetSource(name="a", provider=provider)], fetch=fetch)
            for fetch in (DatasetFetchPolicy.ALWAYS, policy)
        ]
    )
    with (
        patch.object(CentralMemory, "get_memory_instance", return_value=memory),
        pytest.raises(DatasetConstraintError, match="conflicting fetch policies"),
    ):
        await config.prepare_async()
    provider.fetch_dataset_async.assert_not_awaited()
    memory.get_seed_dataset_names_async.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("failure", ["fetch", "cancel", "empty", "dataset-name", "seed-name", "prepare", "insert"])
async def test_refresh_failure_preserves_stored_values_and_ids_async(
    *, sqlite_instance: SQLiteMemory, configured_provider: bool, failure: str
) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="Keep this edit", dataset_name="fresh", origin=SeedOrigin.USER)], added_by="user"
    )
    before = await sqlite_instance.get_seeds_async(dataset_name="fresh")
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "fresh"
    result = dataset("wrong" if failure == "dataset-name" else "fresh", 2)
    if failure == "empty":
        result.seeds = []
    elif failure == "seed-name":
        result.seeds[0].dataset_name = "wrong"
    provider.fetch_dataset_async.return_value = result
    if failure == "fetch":
        provider.fetch_dataset_async.side_effect = RuntimeError("Fetch failed")
    elif failure == "cancel":
        provider.fetch_dataset_async.side_effect = asyncio.CancelledError("Fetch cancelled")
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="fresh", provider=provider if configured_provider else None)],
        fetch=DatasetFetchPolicy.ALWAYS,
    )

    def fail_insert(mapper: object, connection: object, target: SeedEntry) -> None:
        raise SQLAlchemyError("Insert failed")

    preparation = sqlite_instance._prepare_seed_for_storage_async
    if failure == "insert":
        event.listen(SeedEntry, "before_insert", fail_insert)
    try:
        with (
            patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"fresh": provider}),
            patch.object(
                sqlite_instance,
                "_prepare_seed_for_storage_async",
                side_effect=RuntimeError("Preparation failed") if failure == "prepare" else preparation,
            ),
            pytest.raises(
                (RuntimeError, asyncio.CancelledError, DatasetConstraintError, SQLAlchemyError),
                match="failed|cancelled|must return",
            ),
        ):
            await config.prepare_async()
    finally:
        if failure == "insert":
            event.remove(SeedEntry, "before_insert", fail_insert)
    after = await sqlite_instance.get_seeds_async(dataset_name="fresh")
    assert [(seed.id, seed.value, seed.origin, seed.added_by) for seed in after] == [
        (seed.id, seed.value, seed.origin, seed.added_by) for seed in before
    ]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("failure", ["fetch", "invalid", "write"])
async def test_refresh_checks_all_fetches_before_independent_writes_async(
    *, sqlite_instance: SQLiteMemory, failure: str
) -> None:
    for name in ("first", "last"):
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedObjective(value="Original", dataset_name=name)], added_by="test"
        )
    first = MagicMock(spec=SeedDatasetProvider)
    first.dataset_name = "first"
    first.fetch_dataset_async.return_value = dataset("first", 1)
    last = MagicMock(spec=SeedDatasetProvider)
    last.dataset_name = "last"
    last.fetch_dataset_async.return_value = dataset("wrong" if failure == "invalid" else "last", 1)
    if failure == "fetch":
        last.fetch_dataset_async.side_effect = RuntimeError("Last fetch failed")
    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name=provider.dataset_name, provider=provider) for provider in (first, last)],
        fetch=DatasetFetchPolicy.ALWAYS,
    )
    replace = sqlite_instance.replace_prepared_seed_batch_async

    async def replace_async(*, dataset_name: str, batch: PreparedSeedBatch) -> int:
        if failure == "write" and dataset_name == "last":
            raise RuntimeError("Last write failed")
        return await replace(dataset_name=dataset_name, batch=batch)

    with (
        patch.object(sqlite_instance, "replace_prepared_seed_batch_async", side_effect=replace_async),
        pytest.raises((RuntimeError, DatasetConstraintError), match="Last .* failed|must return"),
    ):
        await config.prepare_async()
    assert [seed.value for seed in await sqlite_instance.get_seeds_async(dataset_name="first")] == [
        "first-0" if failure == "write" else "Original"
    ]
    assert [seed.value for seed in await sqlite_instance.get_seeds_async(dataset_name="last")] == ["Original"]


class _CandidateShapingConfiguration(DatasetAttackConfiguration):
    def _build_attack_groups(self, seeds: list[Seed[Any]]) -> list[AttackSeedGroup]:
        if any(seed.value.startswith("invalid") for seed in seeds):
            raise DatasetConstraintError("Candidate shaping failed.")
        if any(seed.value.startswith("empty") for seed in seeds):
            return []
        return super()._build_attack_groups(seeds)


@pytest.mark.parametrize(
    "failure",
    ["group", "minimum", "type", "filter", "filtered-minimum", "shaping", "empty", "compound-child", "compound-total"],
)
async def test_refresh_invalid_population_preserves_values_and_ids_async(
    *, file_memory: SQLiteMemory, failure: str, configured_provider: bool
) -> None:
    memory = file_memory
    for name in ("fresh", "retained"):
        old = dataset(name, 3).seeds
        for seed in old:
            seed.harm_categories = ["keep"]
        await memory.add_seeds_to_memory_async(seeds=old, added_by="user")
    before = {name: await memory.get_seeds_async(dataset_name=name) for name in ("fresh", "retained")}
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "fresh"
    candidate = dataset("fresh", 2)
    if failure == "group":
        shared_id = uuid4()
        for seed in candidate.seeds:
            seed.prompt_group_id = shared_id
    elif failure in ("shaping", "empty"):
        for seed in candidate.seeds:
            seed.value = ("invalid" if failure == "shaping" else "empty") + seed.value
    elif failure == "type":
        candidate = SeedDataset(
            dataset_name="fresh", seeds=[SeedPrompt(value="Not an objective", dataset_name="fresh")]
        )
    elif failure == "filtered-minimum":
        candidate.seeds[0].harm_categories = ["keep"]
        candidate.seeds[1].harm_categories = ["other"]
    provider.fetch_dataset_async.return_value = candidate
    source = DatasetSource(
        name="fresh", fetch=DatasetFetchPolicy.ALWAYS, provider=provider if configured_provider else None
    )
    retained = DatasetSource(name="retained", fetch=DatasetFetchPolicy.NEVER)
    if failure.startswith("compound"):
        config = CompoundDatasetAttackConfiguration(
            configurations=[
                DatasetAttackConfiguration(
                    sources=[source], validators=[require_min_size(3)] if failure == "compound-child" else []
                ),
                DatasetAttackConfiguration(sources=[retained]),
            ],
            validators=[require_min_size(6)] if failure == "compound-total" else [],
            max_total=1,
        )
    else:
        validators = [require_min_size(6)] if failure == "minimum" else []
        if failure == "filtered-minimum":
            validators = [require_min_size(5)]
        elif failure == "type":
            validators = [require_seed_type(SeedObjective)]
        filters = {"harm_categories": ["not_present"]} if failure == "filter" else None
        if failure == "filtered-minimum":
            filters = {"harm_categories": ["keep"]}
        config = _CandidateShapingConfiguration(
            sources=[source] if failure == "empty" else [source, retained],
            validators=validators,
            filters=filters,
            max_total=1,
        )
    with (
        patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"fresh": provider}),
        pytest.raises(ValueError),
    ):
        await config.prepare_async()
    for name, old_seeds in before.items():
        after = await memory.get_seeds_async(dataset_name=name)
        assert {seed.id: seed.value for seed in after} == {seed.id: seed.value for seed in old_seeds}
    assert await memory.get_scenario_results_async() == []
    # The rejected candidates must not remain visible to subsequent reads.
    if failure != "filter":
        assert len(await config.get_attack_seed_groups_async(apply_sampling=False)) >= 3
    else:
        restored = config.with_overrides(filters={"harm_categories": []})
        assert len(await restored.get_attack_seed_groups_async(apply_sampling=False)) == 6


@pytest.mark.parametrize("compound", [False, True])
async def test_refresh_validation_includes_retained_sources_before_sampling_async(
    *, file_memory: SQLiteMemory, compound: bool
) -> None:
    for name in ("fresh", "retained"):
        await file_memory.add_seeds_to_memory_async(seeds=dataset(name, 3).seeds, added_by="user")
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "fresh"
    provider.fetch_dataset_async.return_value = dataset("fresh", 2)
    fresh = DatasetSource(name="fresh", fetch=DatasetFetchPolicy.ALWAYS, provider=provider)
    retained = DatasetSource(name="retained", fetch=DatasetFetchPolicy.NEVER)
    config = (
        CompoundDatasetAttackConfiguration(
            configurations=[
                DatasetAttackConfiguration(sources=[fresh]),
                DatasetAttackConfiguration(sources=[retained]),
            ],
            validators=[require_min_size(5)],
            max_total=1,
        )
        if compound
        else DatasetAttackConfiguration(sources=[fresh, retained], validators=[require_min_size(5)], max_total=1)
    )
    await config.prepare_async()
    assert len(await file_memory.get_seeds_async(dataset_name="fresh")) == 2
    assert len(await config.get_attack_seed_groups_async()) == 1


class _RefreshTechnique(ScenarioTechnique):
    ALL = ("all", {"all"})
    DIRECT = ("direct", set())

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


class _RefreshValidationScenario(Scenario):
    BASELINE_ATTACK_POLICY = BaselineAttackPolicy.Forbidden

    def __init__(self, *, config: DatasetAttackConfiguration) -> None:
        super().__init__(
            version=1,
            technique_class=_RefreshTechnique,
            objective_scorer=SubStringScorer(substring="yes"),
            default_dataset_config=config,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        raise AssertionError("Invalid candidates must fail before attack construction.")


class _PreparedRefreshScenario(_RefreshValidationScenario):
    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        return [
            AtomicAttack(
                atomic_attack_name="fresh",
                attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=context.objective_target)),
                seed_groups=list(context.seed_groups),
            )
        ]


class _JsonSeedProvider(SeedDatasetProvider):
    should_register = False

    def __init__(self, *, path: Path) -> None:
        self.path = path
        self.fetched: SeedDataset | None = None

    @property
    def dataset_name(self) -> str:
        return "fresh"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        async with aiofiles.open(self.path, encoding="utf-8") as handle:
            self.fetched = SeedDataset.model_validate_json(await handle.read())
        return self.fetched


@pytest.mark.parametrize("policy", [DatasetFetchPolicy.IF_MISSING, DatasetFetchPolicy.ALWAYS])
@pytest.mark.parametrize(
    "filters",
    [
        {"value_sha256": [sha256(b"Fresh apple").hexdigest()]},
        {"value_sha256": [sha256(b"Fresh apple").hexdigest()], "harm_categories": ["fruit"]},
    ],
)
async def test_fresh_json_provider_matches_stored_derived_filters_async(
    *, file_memory: SQLiteMemory, tmp_path: Path, policy: DatasetFetchPolicy, filters: dict[str, list[str]]
) -> None:
    source = SeedDataset(
        dataset_name="fresh",
        seeds=[
            SeedObjective(value="Fresh apple", dataset_name="fresh", added_by="provider", harm_categories=["fruit"])
        ],
    )
    path = tmp_path / "fresh.json"
    async with aiofiles.open(path, "w", encoding="utf-8") as handle:
        await handle.write(source.model_dump_json())
    if policy is DatasetFetchPolicy.ALWAYS:
        await file_memory.add_seeds_to_memory_async(seeds=dataset("fresh", 3).seeds, added_by="user")
    provider = _JsonSeedProvider(path=path)
    validated: list[dict[str, Any]] = []

    def validate_prepared(resolved: ResolvedDataset) -> None:
        assert all(seed.added_by == "DatasetConfiguration" and seed.date_added is not None for seed in resolved.seeds)
        validated.extend(seed.model_dump(mode="json") for seed in resolved.seeds)

    config = DatasetAttackConfiguration(
        sources=[DatasetSource(name="fresh", provider=provider)],
        fetch=policy,
        filters=filters,
        validators=[validate_prepared],
    )
    scenario = _PreparedRefreshScenario(config=config)
    scenario.set_params_from_args(args={"objective_target": MockPromptTarget()})
    with patch.object(
        file_memory, "_prepare_seed_for_storage_async", wraps=file_memory._prepare_seed_for_storage_async
    ) as prepare:
        await scenario.initialize_async()
        assert prepare.await_count == 1
    stored = await file_memory.get_seeds_async(dataset_name="fresh", **filters)
    assert len(stored) == 1
    assert stored[0].value == "Fresh apple"
    assert stored[0].value_sha256 == sha256(b"Fresh apple").hexdigest()
    assert stored[0].added_by == "DatasetConfiguration"
    assert stored[0].model_dump(mode="json") == validated[0]
    assert len(await config.get_attack_seed_groups_async()) == 1
    assert provider.fetched is not None
    if policy is DatasetFetchPolicy.ALWAYS:
        assert provider.fetched.seeds[0].value_sha256 is None
        assert provider.fetched.seeds[0].added_by == "provider"


@pytest.mark.parametrize("failure", ["validation", "preparation"])
async def test_refresh_batches_prepare_all_sources_before_any_commit_async(
    *, file_memory: SQLiteMemory, failure: str
) -> None:
    await file_memory.add_seeds_to_memory_async(seeds=dataset("fresh", 3).seeds, added_by="user")
    before = {seed.id: seed.value for seed in await file_memory.get_seeds_async(dataset_name="fresh")}
    providers = []
    for name in ("fresh", "new"):
        provider = MagicMock(spec=SeedDatasetProvider)
        provider.dataset_name = name
        provider.fetch_dataset_async.return_value = dataset(name, 2)
        providers.append(provider)
    config = DatasetAttackConfiguration(
        sources=[
            DatasetSource(name="fresh", fetch=DatasetFetchPolicy.ALWAYS, provider=providers[0]),
            DatasetSource(name="new", provider=providers[1]),
        ],
        validators=[require_min_size(6)] if failure == "validation" else [],
    )
    prepare = file_memory._prepare_seed_for_storage_async

    async def prepare_async(*, prompt: Seed[Any], added_by: str | None, current_time: datetime) -> None:
        if prompt.dataset_name == "new" and failure == "preparation":
            raise RuntimeError("Later seed preparation failed")
        await prepare(prompt=prompt, added_by=added_by, current_time=current_time)

    with (
        patch.object(file_memory, "_prepare_seed_for_storage_async", side_effect=prepare_async),
        pytest.raises((RuntimeError, DatasetConstraintError), match="failed|require at least"),
    ):
        await config.prepare_async()
    assert {seed.id: seed.value for seed in await file_memory.get_seeds_async(dataset_name="fresh")} == before
    assert await file_memory.get_seeds_async(dataset_name="new") == []
    for provider in providers:
        assert all(seed.value_sha256 is None for seed in provider.fetch_dataset_async.return_value.seeds)


@pytest.mark.parametrize("failure", ["group", "minimum"])
async def test_scenario_invalid_refresh_preserves_file_backed_dataset_async(
    *, file_memory: SQLiteMemory, failure: str
) -> None:
    await file_memory.add_seeds_to_memory_async(seeds=dataset("fresh", 3).seeds, added_by="user")
    before = await file_memory.get_seeds_async(dataset_name="fresh")
    candidate = dataset("fresh", 2)
    if failure == "group":
        shared_id = uuid4()
        for seed in candidate.seeds:
            seed.prompt_group_id = shared_id
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = "fresh"
    provider.fetch_dataset_async.return_value = candidate
    scenario = _RefreshValidationScenario(
        config=DatasetAttackConfiguration(
            sources=[DatasetSource(name="fresh", provider=provider)],
            fetch=DatasetFetchPolicy.ALWAYS,
            validators=[require_min_size(3)],
        )
    )
    scenario.set_params_from_args(args={"objective_target": MockPromptTarget()})
    with pytest.raises(ValueError, match="only have one objective|require at least"):
        await scenario.initialize_async()
    after = await file_memory.get_seeds_async(dataset_name="fresh")
    assert {seed.id: seed.value for seed in after} == {seed.id: seed.value for seed in before}
    assert await file_memory.get_scenario_results_async() == []
