# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import warnings
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.analytics import compute_outcome_statistics
from pyrit.analytics.technique_analysis import compute_technique_stats, compute_technique_stats_async
from pyrit.memory import MemoryInterface, SQLiteMemory
from pyrit.models import AttackOutcome, AttackStats, OutcomeStatistics
from unit.memory.test_attack_analytics import make_result


def _make_result(*, eval_hash: str | None, outcome: AttackOutcome) -> MagicMock:
    r = MagicMock()
    if eval_hash is None:
        r.atomic_attack_identifier = None
    else:
        identifier = MagicMock()
        identifier.eval_hash = eval_hash
        r.atomic_attack_identifier = identifier
    r.outcome = outcome
    return r


@pytest.fixture(autouse=True)
def _patch_memory():
    mock_memory = MagicMock(spec=MemoryInterface)
    mock_memory.get_attack_results_async = AsyncMock(return_value=[])
    with patch("pyrit.analytics.technique_analysis.CentralMemory") as cm:
        cm.get_memory_instance.return_value = mock_memory
        yield mock_memory


class TestComputeTechniqueStats:
    async def test_explicit_outcome_statistics_keep_existing_queries_and_default_shape(self, _patch_memory) -> None:
        _patch_memory.get_attack_results_async.return_value = [
            _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
            _make_result(eval_hash="a", outcome=AttackOutcome.ERROR),
            _make_result(eval_hash="b", outcome=AttackOutcome.UNDETERMINED),
        ]
        legacy = await compute_technique_stats_async(technique_eval_hashes=["a", "b"])
        rich = await compute_technique_stats_async(
            technique_eval_hashes=["a", "b"],
            scenario_result_id="run",
            targeted_harm_categories=["privacy"],
            include_outcome_statistics=True,
        )
        assert type(legacy["a"]) is AttackStats
        assert len(asdict(legacy["a"])) == 6
        assert isinstance(rich["a"], OutcomeStatistics)
        assert rich["a"] == compute_outcome_statistics({"success": 1, "error": 1})
        assert rich["a"].success_rate_decided == 1.0
        assert rich["a"].success_rate_all == 0.5
        assert rich["b"].success_rate_decided is None
        assert rich["b"].success_rate_all == 0.0
        assert _patch_memory.get_attack_results_async.call_count == 2
        assert _patch_memory.get_attack_results_async.call_args.kwargs == {
            "atomic_attack_eval_hashes": ["a", "b"],
            "scenario_result_id": "run",
            "targeted_harm_categories": ["privacy"],
        }

    @pytest.mark.parametrize("include_outcome_statistics", [False, True])
    async def test_maintained_async_api_does_not_emit_deprecation(
        self, _patch_memory, include_outcome_statistics: bool
    ) -> None:
        _patch_memory.get_attack_results_async.return_value = [
            _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS)
        ]
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            await compute_technique_stats_async(
                technique_eval_hashes=["a"], include_outcome_statistics=include_outcome_statistics
            )

    def test_sync_wrapper_keeps_existing_deprecation_and_result_shape(self, _patch_memory) -> None:
        _patch_memory.get_attack_results.return_value = [_make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS)]
        with pytest.warns(DeprecationWarning, match=r"compute_technique_stats.*1\.4\.0"):
            statistics = compute_technique_stats(technique_eval_hashes=["a"])
        assert type(statistics["a"]) is AttackStats
        assert len(asdict(statistics["a"])) == 6
        assert statistics["a"].success_rate_decided == 1.0

    async def test_rich_statistics_from_real_saved_results(self, sqlite_instance: SQLiteMemory) -> None:
        first = make_result()
        second = make_result(index=2, outcome=AttackOutcome.ERROR)
        first.conversation_id = "first"
        second.conversation_id = "second"
        await sqlite_instance.add_attack_results_to_memory_async(attack_results=[first, second])
        eval_hash = first.atomic_attack_identifier.eval_hash
        statistics = await compute_technique_stats_async(
            memory=sqlite_instance, technique_eval_hashes=[eval_hash], include_outcome_statistics=True
        )
        assert statistics[eval_hash] == compute_outcome_statistics({"success": 1, "error": 1})
        assert statistics[eval_hash].success_rate_decided == 1.0
        assert statistics[eval_hash].success_rate_all == 0.5

    @pytest.mark.parametrize("hashes", [[], ["absent"]])
    async def test_rich_statistics_preserve_empty_result_semantics(self, _patch_memory, hashes: list[str]) -> None:
        assert await compute_technique_stats_async(technique_eval_hashes=hashes, include_outcome_statistics=True) == {}
        assert _patch_memory.get_attack_results_async.call_count == (1 if hashes else 0)

    async def test_empty_results_returns_empty(self, _patch_memory):
        stats = await compute_technique_stats_async(technique_eval_hashes=["a", "b"])
        assert stats == {}

    async def test_empty_hashes_short_circuits(self, _patch_memory):
        stats = await compute_technique_stats_async(technique_eval_hashes=[])
        assert stats == {}
        _patch_memory.get_attack_results_async.assert_not_called()

    async def test_counts_successes_and_failures(self, _patch_memory):
        _patch_memory.get_attack_results_async = AsyncMock(
            return_value=[
                _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
                _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
                _make_result(eval_hash="a", outcome=AttackOutcome.FAILURE),
                _make_result(eval_hash="b", outcome=AttackOutcome.FAILURE),
            ]
        )

        stats = await compute_technique_stats_async(technique_eval_hashes=["a", "b"])

        assert stats["a"].successes == 2
        assert stats["a"].failures == 1
        assert stats["a"].total_decided == 3
        assert stats["b"].successes == 0
        assert stats["b"].failures == 1

    async def test_counts_errors_and_undetermined(self, _patch_memory):
        _patch_memory.get_attack_results_async = AsyncMock(
            return_value=[
                _make_result(eval_hash="a", outcome=AttackOutcome.ERROR),
                _make_result(eval_hash="a", outcome=AttackOutcome.UNDETERMINED),
            ]
        )

        stats = await compute_technique_stats_async(technique_eval_hashes=["a"])

        assert stats["a"].errors == 1
        assert stats["a"].undetermined == 1

    async def test_ignores_hashes_not_in_requested_list(self, _patch_memory):
        _patch_memory.get_attack_results_async = AsyncMock(
            return_value=[
                _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
                _make_result(eval_hash="c", outcome=AttackOutcome.SUCCESS),
            ]
        )

        stats = await compute_technique_stats_async(technique_eval_hashes=["a", "b"])

        assert "a" in stats
        assert "c" not in stats

    async def test_skips_results_without_eval_hash(self, _patch_memory):
        _patch_memory.get_attack_results_async = AsyncMock(
            return_value=[
                _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
                _make_result(eval_hash=None, outcome=AttackOutcome.SUCCESS),
            ]
        )

        stats = await compute_technique_stats_async(technique_eval_hashes=["a"])

        assert stats["a"].successes == 1

    async def test_passes_eval_hashes_to_memory_query(self, _patch_memory):
        (await compute_technique_stats_async(technique_eval_hashes=["x", "y"]))

        call_kwargs = _patch_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["atomic_attack_eval_hashes"] == ["x", "y"]
        assert call_kwargs["scenario_result_id"] is None
        assert call_kwargs["targeted_harm_categories"] is None

    async def test_passes_scenario_result_id_to_memory_query(self, _patch_memory):
        (await compute_technique_stats_async(technique_eval_hashes=["x"], scenario_result_id="run-123"))

        call_kwargs = _patch_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["scenario_result_id"] == "run-123"

    async def test_omits_hashes_with_no_history(self, _patch_memory):
        _patch_memory.get_attack_results_async = AsyncMock(
            return_value=[
                _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
            ]
        )

        stats = await compute_technique_stats_async(technique_eval_hashes=["a", "b"])

        assert "a" in stats
        assert "b" not in stats

    async def test_success_rate_computed(self, _patch_memory):
        _patch_memory.get_attack_results_async = AsyncMock(
            return_value=[
                _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
                _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
                _make_result(eval_hash="a", outcome=AttackOutcome.FAILURE),
                _make_result(eval_hash="a", outcome=AttackOutcome.FAILURE),
            ]
        )

        stats = await compute_technique_stats_async(technique_eval_hashes=["a"])

        assert stats["a"].success_rate == pytest.approx(0.5)

    async def test_passes_harm_categories_to_memory_query(self, _patch_memory):
        (
            await compute_technique_stats_async(
                technique_eval_hashes=["x"],
                targeted_harm_categories=["misinformation", "hate"],
            )
        )

        call_kwargs = _patch_memory.get_attack_results_async.call_args[1]
        assert call_kwargs["targeted_harm_categories"] == ["misinformation", "hate"]

    async def test_injected_memory_bypasses_central_memory(self, _patch_memory):
        injected = MagicMock()
        injected.get_attack_results_async = AsyncMock(
            return_value=[
                _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
            ]
        )

        stats = await compute_technique_stats_async(technique_eval_hashes=["a"], memory=injected)

        injected.get_attack_results_async.assert_called_once()
        _patch_memory.get_attack_results_async.assert_not_called()
        assert stats["a"].successes == 1
