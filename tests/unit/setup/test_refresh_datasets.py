# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the RefreshDatasets initializer.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import SeedDataset, SeedPrompt
from pyrit.setup.initializers.refresh_datasets import RefreshDatasets


def _make_dataset(*, dataset_name: str, values: list[str], harm_categories: list[str] | None = None) -> SeedDataset:
    seeds = [
        SeedPrompt(
            value=value,
            dataset_name=dataset_name,
            data_type="text",
            harm_categories=harm_categories,
        )
        for value in values
    ]
    return SeedDataset(seeds=seeds, name=dataset_name, dataset_name=dataset_name)


class TestRefreshDatasetsProperties:
    """Property and parameter surface tests."""

    def test_description_mentions_refresh(self) -> None:
        description = RefreshDatasets().description
        assert isinstance(description, str)
        assert "refresh" in description.lower()

    def test_required_env_vars_is_empty(self) -> None:
        assert RefreshDatasets().required_env_vars == []

    def test_supported_parameters_defaults(self) -> None:
        params = {p.name: p for p in RefreshDatasets().supported_parameters}
        assert params["days"].default == RefreshDatasets.DEFAULT_DAYS
        assert params["dataset_names"].default == []
        assert "tags" not in params


class TestRefreshDatasetsParseDays:
    """Validation of the days parameter."""

    def test_default_when_absent(self) -> None:
        initializer = RefreshDatasets()
        initializer.params = {}
        assert initializer._parse_days() == RefreshDatasets.DEFAULT_DAYS

    def test_zero_allowed(self) -> None:
        initializer = RefreshDatasets()
        initializer.params = {"days": ["0"]}
        assert initializer._parse_days() == 0

    @pytest.mark.parametrize("bad", [["-1"], ["abc"], ["3.5"], ["1", "2"], []])
    def test_invalid_days(self, bad: list[str]) -> None:
        initializer = RefreshDatasets()
        # An empty list means "absent" -> default, so only non-empty invalid values raise.
        initializer.params = {"days": bad} if bad else {}
        if bad:
            with pytest.raises(ValueError):
                initializer._parse_days()
        else:
            assert initializer._parse_days() == RefreshDatasets.DEFAULT_DAYS


class TestRefreshDatasetsSelection:
    """Selection precedence and provider-registration filtering (memory mocked)."""

    def _mock_memory(self, *, names_in_memory: list[str]) -> MagicMock:
        memory = MagicMock(spec=MemoryInterface)
        memory.get_seed_dataset_names.return_value = names_in_memory
        return memory

    async def test_empty_memory_returns_without_fetch(self) -> None:
        initializer = RefreshDatasets()
        memory = self._mock_memory(names_in_memory=[])

        with (
            patch.object(CentralMemory, "get_memory_instance", return_value=memory),
            patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch,
        ):
            await initializer.initialize_async()

        mock_fetch.assert_not_called()

    async def test_explicit_names_skip_unregistered_and_not_in_memory(self) -> None:
        initializer = RefreshDatasets()
        initializer.params = {"days": ["0"], "dataset_names": ["in_both", "not_registered", "not_in_memory"]}
        memory = self._mock_memory(names_in_memory=["in_both", "not_registered"])

        with (
            patch.object(CentralMemory, "get_memory_instance", return_value=memory),
            patch.object(
                SeedDatasetProvider,
                "get_all_dataset_names_async",
                new_callable=AsyncMock,
                return_value=["in_both", "not_in_memory"],
            ),
            patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = [_make_dataset(dataset_name="in_both", values=["v"])]

            await initializer.initialize_async()

        # Only "in_both" is both in memory and registered.
        assert mock_fetch.call_count == 1
        assert mock_fetch.call_args.kwargs["dataset_names"] == ["in_both"]
        assert mock_fetch.call_args.kwargs["cache"] is False

    async def test_names_only_consider_registered_and_in_memory(self) -> None:
        # dataset_names selection ignores the tag filter entirely (tags are not a parameter).
        initializer = RefreshDatasets()
        initializer.params = {"days": ["0"], "dataset_names": ["a"]}
        memory = self._mock_memory(names_in_memory=["a", "b"])

        with (
            patch.object(CentralMemory, "get_memory_instance", return_value=memory),
            patch.object(
                SeedDatasetProvider,
                "get_all_dataset_names_async",
                new_callable=AsyncMock,
                return_value=["a", "b"],
            ) as mock_names,
            patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch,
        ):
            mock_fetch.return_value = [_make_dataset(dataset_name="a", values=["v"])]
            await initializer.initialize_async()

        # selection should never consult a tag filter
        for call in mock_names.call_args_list:
            assert call.kwargs.get("filters") is None
        assert mock_fetch.call_args.kwargs["dataset_names"] == ["a"]


@pytest.mark.usefixtures("patch_central_database")
class TestRefreshDatasetsStaleness:
    """Staleness threshold behavior against a real SQLite memory."""

    async def _seed(self, memory: MemoryInterface, *, dataset_name: str, days_old: int) -> None:
        date_added = datetime.now(tz=timezone.utc) - timedelta(days=days_old)
        seed = SeedPrompt(value=f"v-{dataset_name}", dataset_name=dataset_name, data_type="text", date_added=date_added)
        await memory.add_seeds_to_memory_async(seeds=[seed], added_by="seeding")

    async def test_days_zero_refreshes_recent_dataset(self, sqlite_instance: MemoryInterface) -> None:
        await self._seed(sqlite_instance, dataset_name="fresh", days_old=0)
        initializer = RefreshDatasets()
        assert initializer._is_stale(memory=sqlite_instance, dataset_name="fresh", days=0) is True

    async def test_recent_dataset_not_stale(self, sqlite_instance: MemoryInterface) -> None:
        await self._seed(sqlite_instance, dataset_name="fresh", days_old=1)
        initializer = RefreshDatasets()
        assert initializer._is_stale(memory=sqlite_instance, dataset_name="fresh", days=30) is False

    async def test_old_dataset_is_stale(self, sqlite_instance: MemoryInterface) -> None:
        await self._seed(sqlite_instance, dataset_name="old", days_old=40)
        initializer = RefreshDatasets()
        assert initializer._is_stale(memory=sqlite_instance, dataset_name="old", days=30) is True

    async def test_cutoff_is_inclusive(self, sqlite_instance: MemoryInterface) -> None:
        fixed_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        at_cutoff = fixed_now - timedelta(days=30)
        just_newer = at_cutoff + timedelta(microseconds=1)

        seed_at = SeedPrompt(value="at", dataset_name="at_cutoff", data_type="text", date_added=at_cutoff)
        seed_new = SeedPrompt(value="new", dataset_name="just_newer", data_type="text", date_added=just_newer)
        await sqlite_instance.add_seeds_to_memory_async(seeds=[seed_at, seed_new], added_by="seeding")

        initializer = RefreshDatasets()
        with patch("pyrit.setup.initializers.refresh_datasets.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            assert initializer._is_stale(memory=sqlite_instance, dataset_name="at_cutoff", days=30) is True
            assert initializer._is_stale(memory=sqlite_instance, dataset_name="just_newer", days=30) is False

    async def test_no_op_when_all_fresh(self, sqlite_instance: MemoryInterface) -> None:
        await self._seed(sqlite_instance, dataset_name="fresh", days_old=1)
        initializer = RefreshDatasets()
        initializer.params = {"days": ["30"]}

        with (
            patch.object(
                SeedDatasetProvider,
                "get_all_dataset_names_async",
                new_callable=AsyncMock,
                return_value=["fresh"],
            ),
            patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as mock_fetch,
        ):
            await initializer.initialize_async()

        mock_fetch.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
class TestRefreshDatasetsRefreshCorrectness:
    """End-to-end replace semantics against a real SQLite memory (only the provider is mocked)."""

    async def _run_refresh(self, *, new_dataset: SeedDataset, dataset_name: str) -> None:
        initializer = RefreshDatasets()
        initializer.params = {"days": ["0"]}
        with (
            patch.object(
                SeedDatasetProvider,
                "get_all_dataset_names_async",
                new_callable=AsyncMock,
                return_value=[dataset_name],
            ),
            patch.object(
                SeedDatasetProvider,
                "fetch_datasets_async",
                new_callable=AsyncMock,
                return_value=[new_dataset],
            ),
        ):
            await initializer.initialize_async()

    async def test_metadata_only_change_replaces_row(self, sqlite_instance: MemoryInterface) -> None:
        old = SeedPrompt(value="same-value", dataset_name="d", data_type="text", harm_categories=["oldharm"])
        await sqlite_instance.add_seeds_to_memory_async(seeds=[old], added_by="seeding")

        new_dataset = _make_dataset(dataset_name="d", values=["same-value"], harm_categories=["newharm"])
        await self._run_refresh(new_dataset=new_dataset, dataset_name="d")

        result = sqlite_instance.get_seeds(dataset_name="d")
        assert len(result) == 1
        assert result[0].harm_categories == ["newharm"]

    async def test_value_change_replaces_row(self, sqlite_instance: MemoryInterface) -> None:
        old = SeedPrompt(value="v1", dataset_name="d", data_type="text")
        await sqlite_instance.add_seeds_to_memory_async(seeds=[old], added_by="seeding")

        new_dataset = _make_dataset(dataset_name="d", values=["v2"])
        await self._run_refresh(new_dataset=new_dataset, dataset_name="d")

        result = sqlite_instance.get_seeds(dataset_name="d")
        assert len(result) == 1
        assert result[0].value == "v2"

    async def test_upstream_removed_seed_disappears(self, sqlite_instance: MemoryInterface) -> None:
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[
                SeedPrompt(value="v1", dataset_name="d", data_type="text"),
                SeedPrompt(value="v2", dataset_name="d", data_type="text"),
            ],
            added_by="seeding",
        )

        new_dataset = _make_dataset(dataset_name="d", values=["v1"])
        await self._run_refresh(new_dataset=new_dataset, dataset_name="d")

        values = {seed.value for seed in sqlite_instance.get_seeds(dataset_name="d")}
        assert values == {"v1"}

    async def test_other_datasets_untouched(self, sqlite_instance: MemoryInterface) -> None:
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[
                SeedPrompt(value="keep", dataset_name="other", data_type="text"),
                SeedPrompt(value="old", dataset_name="d", data_type="text"),
            ],
            added_by="seeding",
        )

        new_dataset = _make_dataset(dataset_name="d", values=["new"])
        await self._run_refresh(new_dataset=new_dataset, dataset_name="d")

        assert {s.value for s in sqlite_instance.get_seeds(dataset_name="other")} == {"keep"}
        assert {s.value for s in sqlite_instance.get_seeds(dataset_name="d")} == {"new"}

    async def test_failed_fetch_leaves_existing_seeds_intact(self, sqlite_instance: MemoryInterface) -> None:
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedPrompt(value="v1", dataset_name="d", data_type="text")],
            added_by="seeding",
        )

        initializer = RefreshDatasets()
        initializer.params = {"days": ["0"]}
        with (
            patch.object(
                SeedDatasetProvider, "get_all_dataset_names_async", new_callable=AsyncMock, return_value=["d"]
            ),
            patch.object(
                SeedDatasetProvider,
                "fetch_datasets_async",
                new_callable=AsyncMock,
                side_effect=RuntimeError("network down"),
            ),
        ):
            await initializer.initialize_async()

        # Fetch failed before delete -> the original seed is still present.
        assert {s.value for s in sqlite_instance.get_seeds(dataset_name="d")} == {"v1"}

    async def test_no_dataset_returned_does_not_wipe_dataset(self, sqlite_instance: MemoryInterface) -> None:
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedPrompt(value="v1", dataset_name="d", data_type="text")],
            added_by="seeding",
        )

        initializer = RefreshDatasets()
        initializer.params = {"days": ["0"]}
        with (
            patch.object(
                SeedDatasetProvider, "get_all_dataset_names_async", new_callable=AsyncMock, return_value=["d"]
            ),
            patch.object(
                SeedDatasetProvider,
                "fetch_datasets_async",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            await initializer.initialize_async()

        assert {s.value for s in sqlite_instance.get_seeds(dataset_name="d")} == {"v1"}

    async def test_empty_dataset_does_not_wipe_dataset(self, sqlite_instance: MemoryInterface) -> None:
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedPrompt(value="v1", dataset_name="d", data_type="text")],
            added_by="seeding",
        )

        # SeedDataset validation forbids empty seeds, so use a spec'd stand-in to exercise the guard.
        empty_dataset = MagicMock(spec=SeedDataset)
        empty_dataset.seeds = []
        initializer = RefreshDatasets()
        initializer.params = {"days": ["0"]}
        with (
            patch.object(
                SeedDatasetProvider, "get_all_dataset_names_async", new_callable=AsyncMock, return_value=["d"]
            ),
            patch.object(
                SeedDatasetProvider,
                "fetch_datasets_async",
                new_callable=AsyncMock,
                return_value=[empty_dataset],
            ),
        ):
            await initializer.initialize_async()

        assert {s.value for s in sqlite_instance.get_seeds(dataset_name="d")} == {"v1"}

    async def test_insert_failure_preserves_existing_seeds(self, sqlite_instance: MemoryInterface) -> None:
        # The initializer isolates a failed replace: replace_seeds_for_dataset_async is mocked to
        # raise before touching storage, so the existing seeds stay intact and the dataset stays
        # selectable for a later retry. (Atomicity of the replace itself -- rolling the delete back
        # with a failed insert -- is covered at the memory layer by
        # test_replace_seeds_for_dataset_async_rolls_back_on_error.)
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedPrompt(value="v1", dataset_name="d", data_type="text")],
            added_by="seeding",
        )
        new_dataset = _make_dataset(dataset_name="d", values=["v2"])

        initializer = RefreshDatasets()
        initializer.params = {"days": ["0"]}

        with (
            patch.object(
                SeedDatasetProvider, "get_all_dataset_names_async", new_callable=AsyncMock, return_value=["d"]
            ),
            patch.object(
                SeedDatasetProvider,
                "fetch_datasets_async",
                new_callable=AsyncMock,
                return_value=[new_dataset],
            ),
            patch.object(
                sqlite_instance,
                "replace_seeds_for_dataset_async",
                new_callable=AsyncMock,
                side_effect=RuntimeError("insert failed"),
            ),
        ):
            await initializer.initialize_async()

        # The failed refresh left the original seed untouched (the initializer did not wipe it).
        assert {s.value for s in sqlite_instance.get_seeds(dataset_name="d")} == {"v1"}

        # The dataset is still in memory, so a later successful run refreshes it.
        await self._run_refresh(new_dataset=new_dataset, dataset_name="d")
        assert {s.value for s in sqlite_instance.get_seeds(dataset_name="d")} == {"v2"}

    async def test_mismatched_dataset_name_does_not_replace(self, sqlite_instance: MemoryInterface) -> None:
        # Guard against a provider returning seeds tagged with a different dataset_name, which would
        # otherwise delete the requested dataset and insert unrelated seeds under another name.
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedPrompt(value="v1", dataset_name="d", data_type="text")],
            added_by="seeding",
        )
        wrong = _make_dataset(dataset_name="other", values=["x"])

        initializer = RefreshDatasets()
        initializer.params = {"days": ["0"]}
        with (
            patch.object(
                SeedDatasetProvider, "get_all_dataset_names_async", new_callable=AsyncMock, return_value=["d"]
            ),
            patch.object(
                SeedDatasetProvider,
                "fetch_datasets_async",
                new_callable=AsyncMock,
                return_value=[wrong],
            ),
        ):
            await initializer.initialize_async()

        # The guard rejected the mismatched dataset -> original seeds preserved, nothing leaked.
        assert {s.value for s in sqlite_instance.get_seeds(dataset_name="d")} == {"v1"}
        assert sqlite_instance.get_seeds(dataset_name="other") == []
