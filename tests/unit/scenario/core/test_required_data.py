# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Required data: datasets a scenario reads in full, apart from the population it limits."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.datasets import SeedDatasetProvider
from pyrit.executor.attack import PromptSendingAttack
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AttackSeedGroup,
    DatasetFetchPolicy,
    DatasetSelectionMode,
    ScenarioDatasetOptions,
    ScenarioDatasetRequest,
    SeedDataset,
    SeedObjective,
)
from pyrit.scenario import DatasetAttackConfiguration, DatasetSource, Scenario, ScenarioRequiredData
from pyrit.scenario.core import AtomicAttack, AttackTechnique, BaselineAttackPolicy, ScenarioTechnique
from pyrit.scenario.core.dataset_configuration import DatasetConstraintError, ResolvedDataset
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.score import SubStringScorer


def _dataset(name: str, count: int = 3) -> SeedDataset:
    return SeedDataset(
        dataset_name=name,
        seeds=[SeedObjective(value=f"{name}-{index}", dataset_name=name) for index in range(count)],
    )


def _provider(dataset: SeedDataset) -> MagicMock:
    provider = MagicMock(spec=SeedDatasetProvider)
    provider.dataset_name = dataset.dataset_name
    provider.fetch_dataset_async = AsyncMock(return_value=dataset)
    return provider


def _require_count(count: int) -> Callable[[ResolvedDataset], None]:
    def _validate(resolved: ResolvedDataset) -> None:
        if len(resolved.seeds) != count:
            raise DatasetConstraintError(f"expected {count} seeds")

    return _validate


@pytest.mark.parametrize("max_size", [1, 5])
def test_required_sources_cannot_be_capped(*, max_size: int) -> None:
    with pytest.raises(DatasetConstraintError, match="read in full"):
        ScenarioRequiredData(sources=[DatasetSource(name="a"), DatasetSource(name="b", max_size=max_size)])


def test_required_data_needs_a_source() -> None:
    with pytest.raises(DatasetConstraintError, match="at least one"):
        ScenarioRequiredData(sources=[])


@pytest.mark.parametrize("max_size", ["default", "all", None])
def test_uncapped_sources_are_accepted(*, max_size: str | None) -> None:
    required = ScenarioRequiredData(sources=[DatasetSource(name="a", max_size=max_size)])

    assert required.dataset_names == ["a"]
    assert required.fetch is DatasetFetchPolicy.IF_MISSING


@pytest.mark.usefixtures("patch_central_database")
async def test_prepare_stores_once_and_load_reads_everything_without_fetching_async(
    *, sqlite_instance: SQLiteMemory
) -> None:
    provider = _provider(_dataset("a", count=12))
    required = ScenarioRequiredData(sources=[DatasetSource(name="a", provider=provider)])

    await required.prepare_async()
    await required.prepare_async()
    first = await required.load_async()
    second = await required.load_async()

    provider.fetch_dataset_async.assert_awaited_once()
    assert len(first["a"]) == len(second["a"]) == 12
    assert len(await sqlite_instance.get_seeds_async(dataset_name="a")) == 12


@pytest.mark.usefixtures("patch_central_database")
async def test_load_never_fetches_a_missing_dataset_async() -> None:
    provider = _provider(_dataset("a"))
    required = ScenarioRequiredData(sources=[DatasetSource(name="a", provider=provider)])

    with pytest.raises(DatasetConstraintError, match="could not be loaded"):
        await required.load_async()
    provider.fetch_dataset_async.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
async def test_never_policy_requires_stored_data_async() -> None:
    provider = _provider(_dataset("a"))
    required = ScenarioRequiredData(
        sources=[DatasetSource(name="a", provider=provider)], fetch=DatasetFetchPolicy.NEVER
    )

    with pytest.raises(DatasetConstraintError, match="fetch is 'never'"):
        await required.prepare_async()
    provider.fetch_dataset_async.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
async def test_validators_run_on_every_read_async(*, sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[_dataset("a", count=2)], added_by="test")
    required = ScenarioRequiredData(sources=[DatasetSource(name="a")], validators=[_require_count(3)])

    with pytest.raises(DatasetConstraintError, match="expected 3 seeds"):
        await required.load_async()


@pytest.mark.usefixtures("patch_central_database")
async def test_refresh_replaces_the_complete_dataset_async(*, sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[_dataset("a", count=5)], added_by="test")
    provider = _provider(SeedDataset(dataset_name="a", seeds=[SeedObjective(value="fresh", dataset_name="a")]))
    required = ScenarioRequiredData(sources=[DatasetSource(name="a", provider=provider)])

    await required.prepare_async(fetch=DatasetFetchPolicy.ALWAYS)

    assert [seed.value for seed in (await required.load_async())["a"]] == ["fresh"]


@pytest.mark.usefixtures("patch_central_database")
async def test_refresh_that_fails_validation_keeps_stored_data_async(*, sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[_dataset("a", count=3)], added_by="test")
    provider = _provider(_dataset("a", count=1))
    required = ScenarioRequiredData(
        sources=[DatasetSource(name="a", provider=provider)],
        fetch=DatasetFetchPolicy.ALWAYS,
        validators=[_require_count(3)],
    )

    with pytest.raises(DatasetConstraintError, match="expected 3 seeds"):
        await required.prepare_async()

    provider.fetch_dataset_async.assert_awaited_once()
    assert len(await sqlite_instance.get_seeds_async(dataset_name="a")) == 3


@pytest.mark.usefixtures("patch_central_database")
async def test_assembly_check_reads_candidates_before_any_write_async(*, sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[_dataset("a", count=3)], added_by="test")
    provider = _provider(_dataset("a", count=1))
    required = ScenarioRequiredData(
        sources=[DatasetSource(name="a", provider=provider)],
        fetch=DatasetFetchPolicy.ALWAYS,
    )
    seen: list[int] = []

    async def reject_after_reading_async() -> None:
        seen.append(len((await required.load_async())["a"]))
        raise DatasetConstraintError("assembly rejected the refresh")

    with pytest.raises(DatasetConstraintError, match="assembly rejected the refresh"):
        await required.prepare_async(validate_assembly=reject_after_reading_async)

    # The check saw the candidate (1 seed), not the stored data, and the rejection kept the stored 3.
    assert seen == [1]
    assert [seed.value for seed in (await required.load_async())["a"]] == ["a-0", "a-1", "a-2"]


@pytest.mark.usefixtures("patch_central_database")
async def test_filters_narrow_the_rows_that_are_read_async(*, sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[
            SeedObjective(value="kept", dataset_name="a", harm_categories=["cyber"]),
            SeedObjective(value="dropped", dataset_name="a", harm_categories=["violence"]),
        ],
        added_by="test",
    )
    required = ScenarioRequiredData(sources=[DatasetSource(name="a")], filters={"harm_categories": ["cyber"]})

    assert [seed.value for seed in (await required.load_async())["a"]] == ["kept"]


@pytest.mark.usefixtures("patch_central_database")
async def test_filters_that_match_nothing_fail_clearly_async(*, sqlite_instance: SQLiteMemory) -> None:
    await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[_dataset("a")], added_by="test")
    required = ScenarioRequiredData(sources=[DatasetSource(name="a")], filters={"harm_categories": ["missing"]})

    with pytest.raises(DatasetConstraintError, match="none match the configured filters"):
        await required.load_async()


def test_projection_lists_every_source_uncapped() -> None:
    provider = _provider(_dataset("b"))
    required = ScenarioRequiredData(
        sources=[DatasetSource(name="a"), DatasetSource(name="b", fetch=DatasetFetchPolicy.NEVER, provider=provider)]
    )

    projected = [
        (source.name, source.max_size, source.fetch, source.configured_provider)
        for source in required.project_sources()
    ]

    assert projected == [
        ("a", "all", DatasetFetchPolicy.IF_MISSING, False),
        ("b", "all", DatasetFetchPolicy.NEVER, True),
    ]


class TestApplyOverrides:
    _FIXED = ScenarioDatasetOptions(mode=DatasetSelectionMode.FIXED, per_dataset_limit=False)
    _ANY = ScenarioDatasetOptions()

    def _required(self) -> ScenarioRequiredData:
        return ScenarioRequiredData(sources=[DatasetSource(name="a"), DatasetSource(name="b")])

    @pytest.mark.parametrize("with_sources", [False, True])
    def test_global_fetch_reaches_required_sources_and_stays_for_the_population(self, *, with_sources: bool) -> None:
        original = ScenarioRequiredData(
            sources=[DatasetSource(name="a"), DatasetSource(name="b", fetch=DatasetFetchPolicy.IF_MISSING)]
        )
        sources = [{"name": "a"}, {"name": "b"}, {"name": "c"}] if with_sources else None
        request = ScenarioDatasetRequest.model_validate({"sources": sources, "fetch": "never", "max_total": 3})

        required, remaining = original.apply_overrides(request=request, options=self._ANY, population_names=["c"])

        # "a" has no policy of its own, so it takes the caller's; "b" keeps its explicit one.
        assert required.fetch is DatasetFetchPolicy.NEVER
        assert [(source.name, source.fetch) for source in required.sources] == [
            ("a", None),
            ("b", DatasetFetchPolicy.IF_MISSING),
        ]
        assert remaining.fetch is DatasetFetchPolicy.NEVER
        assert remaining.max_total == 3

    @pytest.mark.parametrize("with_sources", [False, True])
    def test_filters_stay_on_the_request_for_the_population(self, *, with_sources: bool) -> None:
        sources = [{"name": "a"}, {"name": "b"}] if with_sources else None
        request = ScenarioDatasetRequest.model_validate({"sources": sources, "filters": {"harm_categories": ["cyber"]}})

        _, remaining = self._required().apply_overrides(request=request, options=self._FIXED, population_names=[])

        assert remaining.filters == {"harm_categories": ["cyber"]}

    def test_with_sources_merges_filters_and_keeps_validators(self) -> None:
        def validator(_: ResolvedDataset) -> None:
            pass

        original = ScenarioRequiredData(
            sources=[DatasetSource(name="a")], validators=[validator], filters={"data_types": ["text"]}
        )

        copied = original.with_sources(
            sources=original.sources, fetch=DatasetFetchPolicy.NEVER, filters={"harm_categories": ["cyber"]}
        )

        assert copied.filters == {"data_types": ["text"], "harm_categories": ["cyber"]}
        assert copied.fetch is DatasetFetchPolicy.NEVER
        assert copied._validators == [validator]

    def test_empty_request_keeps_the_declaration(self) -> None:
        original = self._required()

        required, remaining = original.apply_overrides(
            request=ScenarioDatasetRequest(), options=self._FIXED, population_names=[]
        )

        assert required is original
        assert remaining == ScenarioDatasetRequest()

    def test_source_fetch_edits_reach_only_their_dataset(self) -> None:
        request = ScenarioDatasetRequest.model_validate({"sources": [{"name": "a"}, {"name": "b", "fetch": "always"}]})

        required, remaining = self._required().apply_overrides(
            request=request, options=self._FIXED, population_names=[]
        )

        assert [(source.name, source.fetch) for source in required.sources] == [
            ("a", None),
            ("b", DatasetFetchPolicy.ALWAYS),
        ]
        assert remaining.sources is None

    def test_population_sources_pass_through(self) -> None:
        request = ScenarioDatasetRequest.model_validate({"sources": [{"name": "a"}, {"name": "b"}, {"name": "c"}]})

        _, remaining = self._required().apply_overrides(request=request, options=self._ANY, population_names=["c"])

        assert [source.name for source in remaining.sources or []] == ["c"]

    @pytest.mark.parametrize(
        ("sources", "options", "error"),
        [
            ([{"name": "b"}, {"name": "a"}], _FIXED, "fixed: a, b"),
            ([{"name": "a"}], _FIXED, "fixed: a, b"),
            ([{"name": "a"}, {"name": "b"}, {"name": "c"}], _FIXED, "fixed: a, b"),
            ([{"name": "a"}, {"name": "c"}], _ANY, "can't be deselected: b"),
            ([{"name": "a", "max_size": 2}, {"name": "b"}], _ANY, "can't be capped"),
            ([{"name": "a", "max_size": "all"}, {"name": "b"}], _ANY, "can't be capped"),
            ([{"name": "a"}, {"name": "b"}], _ANY, r"at least one population dataset .*defaults: c"),
        ],
    )
    def test_unsupported_edits_fail_clearly(
        self, *, sources: list[dict[str, object]], options: ScenarioDatasetOptions, error: str
    ) -> None:
        request = ScenarioDatasetRequest.model_validate({"sources": sources})
        population = ["c"] if options is self._ANY else []

        with pytest.raises(DatasetConstraintError, match=error):
            self._required().apply_overrides(request=request, options=options, population_names=population)

    def test_scenarios_without_overrides_reject_required_edits(self) -> None:
        request = ScenarioDatasetRequest.model_validate({"sources": [{"name": "a"}, {"name": "b"}]})

        with pytest.raises(DatasetConstraintError, match="does not support dataset overrides"):
            self._required().apply_overrides(
                request=request,
                options=ScenarioDatasetOptions(supports_overrides=False),
                population_names=[],
            )


@pytest.mark.usefixtures("patch_central_database")
async def test_registered_provider_is_used_when_none_is_configured_async(*, sqlite_instance: SQLiteMemory) -> None:
    provider = _provider(_dataset("a"))
    required = ScenarioRequiredData(sources=[DatasetSource(name="a")])

    with patch.object(SeedDatasetProvider, "get_providers_by_name_async", return_value={"a": provider}) as lookup:
        await required.prepare_async()

    lookup.assert_awaited_once()
    assert len(await sqlite_instance.get_seeds_async(dataset_name="a")) == 3


def _versioned(name: str, version: str) -> SeedDataset:
    return SeedDataset(
        dataset_name=name,
        seeds=[SeedObjective(value=f"{version}-{name}-{index}", dataset_name=name) for index in range(2)],
    )


class _Technique(ScenarioTechnique):
    ALL = ("all", {"all"})
    DIRECT = ("direct", set())

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


class _TemplatesAndRecordsScenario(Scenario):
    """Required ``templates`` and a selectable ``records`` population that only assemble at the same version."""

    BASELINE_ATTACK_POLICY = BaselineAttackPolicy.Forbidden

    def __init__(self, *, templates: MagicMock, records: MagicMock) -> None:
        self._templates = templates
        super().__init__(
            version=1,
            technique_class=_Technique,
            objective_scorer=SubStringScorer(substring="yes"),
            default_dataset_config=DatasetAttackConfiguration(
                sources=[DatasetSource(name="records", provider=records)]
            ),
        )

    def get_required_data(self) -> ScenarioRequiredData:
        return ScenarioRequiredData(sources=[DatasetSource(name="templates", provider=self._templates)])

    async def _resolve_seed_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        assert self._required_data is not None
        templates = (await self._required_data.load_async())["templates"]
        groups = await self._dataset_config.get_attack_groups_by_dataset_async(apply_sampling=apply_sampling)
        versions = {seed.value.split("-")[0] for seed in templates} | {
            seed.value.split("-")[0] for group in groups["records"] for seed in group.seeds
        }
        if len(versions) != 1:
            raise DatasetConstraintError(f"templates and records disagree: {sorted(versions)}")
        return groups

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        return [
            AtomicAttack(
                atomic_attack_name="direct",
                attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=context.objective_target)),
                seed_groups=list(context.seed_groups),
            )
        ]


async def _stored_async(memory: SQLiteMemory, name: str) -> dict[str, str]:
    return {str(seed.id): str(seed.value) for seed in await memory.get_seeds_async(dataset_name=name)}


async def _initialize_async(*, templates: SeedDataset, records: SeedDataset, overrides: dict[str, Any]) -> None:
    scenario = _TemplatesAndRecordsScenario(templates=_provider(templates), records=_provider(records))
    scenario.set_params_from_args(args={"objective_target": MockPromptTarget(), "dataset_overrides": overrides})
    await scenario.initialize_async()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("records_stored", [False, True])
async def test_required_refresh_validates_with_population_candidates_async(
    *, sqlite_instance: SQLiteMemory, records_stored: bool
) -> None:
    # A templates refresh must see the records being fetched (or refreshed) in the same run, not
    # missing or stale records. Refreshing both to v2 together is a valid joint refresh.
    await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[_versioned("templates", "v1")], added_by="test")
    if records_stored:
        await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[_versioned("records", "v1")], added_by="test")

    await _initialize_async(
        templates=_versioned("templates", "v2"),
        records=_versioned("records", "v2"),
        overrides={"fetch": "always"},
    )

    assert sorted((await _stored_async(sqlite_instance, "templates")).values())[0].startswith("v2")
    assert sorted((await _stored_async(sqlite_instance, "records")).values())[0].startswith("v2")


@pytest.mark.usefixtures("patch_central_database")
async def test_population_refresh_rejected_by_assembly_keeps_stored_rows_async(
    *, sqlite_instance: SQLiteMemory
) -> None:
    for name in ("templates", "records"):
        await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[_versioned(name, "v1")], added_by="test")
    before = {name: await _stored_async(sqlite_instance, name) for name in ("templates", "records")}

    with pytest.raises(DatasetConstraintError, match="disagree"):
        await _initialize_async(
            templates=_versioned("templates", "v1"),
            records=_versioned("records", "v2"),
            overrides={"sources": [{"name": "templates"}, {"name": "records", "fetch": "always"}]},
        )

    # Same values and same ids, so earlier runs that planned these rows can still resume.
    assert {name: await _stored_async(sqlite_instance, name) for name in ("templates", "records")} == before


@pytest.mark.usefixtures("patch_central_database")
async def test_global_never_fetch_reaches_required_data_with_population_sources_async(
    *, sqlite_instance: SQLiteMemory
) -> None:
    await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[_versioned("records", "v1")], added_by="test")
    templates = _provider(_versioned("templates", "v1"))
    scenario = _TemplatesAndRecordsScenario(templates=templates, records=_provider(_versioned("records", "v1")))
    scenario.set_params_from_args(
        args={"objective_target": MockPromptTarget(), "dataset_overrides": {"fetch": "never"}}
    )

    with pytest.raises(DatasetConstraintError, match="'templates' is missing and fetch is 'never'"):
        await scenario.initialize_async()

    templates.fetch_dataset_async.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
async def test_selecting_only_required_data_does_not_restore_the_default_population_async() -> None:
    with pytest.raises(DatasetConstraintError, match="at least one population dataset"):
        await _initialize_async(
            templates=_versioned("templates", "v1"),
            records=_versioned("records", "v1"),
            overrides={"sources": [{"name": "templates"}]},
        )
