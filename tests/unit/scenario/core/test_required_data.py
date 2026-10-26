# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Required data: datasets a scenario reads in full, apart from the population it limits."""

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    DatasetFetchPolicy,
    DatasetSelectionMode,
    ScenarioDatasetOptions,
    ScenarioDatasetRequest,
    SeedDataset,
    SeedObjective,
)
from pyrit.scenario import DatasetSource, ScenarioRequiredData
from pyrit.scenario.core.dataset_configuration import DatasetConstraintError, ResolvedDataset


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

    def test_global_fetch_applies_and_stays_for_the_population(self) -> None:
        request = ScenarioDatasetRequest(fetch=DatasetFetchPolicy.ALWAYS, max_total=3)

        required, remaining = self._required().apply_overrides(
            request=request, options=self._FIXED, population_names=[]
        )

        assert required.fetch is DatasetFetchPolicy.ALWAYS
        assert remaining == request

    @pytest.mark.parametrize("with_sources", [False, True])
    def test_filters_apply_to_required_data_and_stay_for_the_population(self, *, with_sources: bool) -> None:
        sources = [{"name": "a"}, {"name": "b"}] if with_sources else None
        request = ScenarioDatasetRequest.model_validate({"sources": sources, "filters": {"harm_categories": ["cyber"]}})

        required, remaining = self._required().apply_overrides(
            request=request, options=self._FIXED, population_names=[]
        )

        assert required.filters == {"harm_categories": ["cyber"]}
        assert remaining.filters == {"harm_categories": ["cyber"]}

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
        ],
    )
    def test_unsupported_edits_fail_clearly(
        self, *, sources: list[dict[str, object]], options: ScenarioDatasetOptions, error: str
    ) -> None:
        request = ScenarioDatasetRequest.model_validate({"sources": sources})

        with pytest.raises(DatasetConstraintError, match=error):
            self._required().apply_overrides(request=request, options=options, population_names=[])

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
