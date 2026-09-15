# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from pyrit.analytics.technique_analysis import compute_labeled_technique_stats, compute_technique_stats
from pyrit.models import AttackOutcome


def _make_result(*, eval_hash: str | None, outcome: AttackOutcome) -> MagicMock:
    r = MagicMock()
    r.attack_result_id = str(uuid4())
    if eval_hash is None:
        r.atomic_attack_identifier = None
    else:
        identifier = MagicMock()
        identifier.eval_hash = eval_hash
        r.atomic_attack_identifier = identifier
    r.outcome = outcome
    r.labels = {}
    return r


def _make_labeled_result(*, label_name: str, technique_identifier: str, outcome: AttackOutcome) -> MagicMock:
    result = _make_result(eval_hash=None, outcome=outcome)
    result.labels = {label_name: technique_identifier}
    return result


@pytest.fixture(autouse=True)
def _patch_memory():
    mock_memory = MagicMock()
    mock_memory.get_attack_results.return_value = []
    with patch("pyrit.analytics.technique_analysis.CentralMemory") as cm:
        cm.get_memory_instance.return_value = mock_memory
        yield mock_memory


class TestComputeTechniqueStats:
    def test_empty_results_returns_empty(self, _patch_memory):
        stats = compute_technique_stats(technique_eval_hashes=["a", "b"])
        assert stats == {}

    def test_empty_hashes_short_circuits(self, _patch_memory):
        stats = compute_technique_stats(technique_eval_hashes=[])
        assert stats == {}
        _patch_memory.get_attack_results.assert_not_called()

    def test_counts_successes_and_failures(self, _patch_memory):
        _patch_memory.get_attack_results.return_value = [
            _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
            _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
            _make_result(eval_hash="a", outcome=AttackOutcome.FAILURE),
            _make_result(eval_hash="b", outcome=AttackOutcome.FAILURE),
        ]

        stats = compute_technique_stats(technique_eval_hashes=["a", "b"])

        assert stats["a"].successes == 2
        assert stats["a"].failures == 1
        assert stats["a"].total_decided == 3
        assert stats["b"].successes == 0
        assert stats["b"].failures == 1

    def test_counts_errors_and_undetermined(self, _patch_memory):
        _patch_memory.get_attack_results.return_value = [
            _make_result(eval_hash="a", outcome=AttackOutcome.ERROR),
            _make_result(eval_hash="a", outcome=AttackOutcome.UNDETERMINED),
        ]

        stats = compute_technique_stats(technique_eval_hashes=["a"])

        assert stats["a"].errors == 1
        assert stats["a"].undetermined == 1

    def test_ignores_hashes_not_in_requested_list(self, _patch_memory):
        _patch_memory.get_attack_results.return_value = [
            _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
            _make_result(eval_hash="c", outcome=AttackOutcome.SUCCESS),
        ]

        stats = compute_technique_stats(technique_eval_hashes=["a", "b"])

        assert "a" in stats
        assert "c" not in stats

    def test_skips_results_without_eval_hash(self, _patch_memory):
        _patch_memory.get_attack_results.return_value = [
            _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
            _make_result(eval_hash=None, outcome=AttackOutcome.SUCCESS),
        ]

        stats = compute_technique_stats(technique_eval_hashes=["a"])

        assert stats["a"].successes == 1

    def test_passes_eval_hashes_to_memory_query(self, _patch_memory):
        compute_technique_stats(technique_eval_hashes=["x", "y"])

        call_kwargs = _patch_memory.get_attack_results.call_args[1]
        assert call_kwargs["atomic_attack_eval_hashes"] == ["x", "y"]
        assert call_kwargs["scenario_result_id"] is None
        assert call_kwargs["targeted_harm_categories"] is None

    def test_passes_scenario_result_id_to_memory_query(self, _patch_memory):
        compute_technique_stats(technique_eval_hashes=["x"], scenario_result_id="run-123")

        call_kwargs = _patch_memory.get_attack_results.call_args[1]
        assert call_kwargs["scenario_result_id"] == "run-123"

    def test_omits_hashes_with_no_history(self, _patch_memory):
        _patch_memory.get_attack_results.return_value = [
            _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
        ]

        stats = compute_technique_stats(technique_eval_hashes=["a", "b"])

        assert "a" in stats
        assert "b" not in stats

    def test_success_rate_computed(self, _patch_memory):
        _patch_memory.get_attack_results.return_value = [
            _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
            _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
            _make_result(eval_hash="a", outcome=AttackOutcome.FAILURE),
            _make_result(eval_hash="a", outcome=AttackOutcome.FAILURE),
        ]

        stats = compute_technique_stats(technique_eval_hashes=["a"])

        assert stats["a"].success_rate == pytest.approx(0.5)

    def test_passes_harm_categories_to_memory_query(self, _patch_memory):
        compute_technique_stats(
            technique_eval_hashes=["x"],
            targeted_harm_categories=["misinformation", "hate"],
        )

        call_kwargs = _patch_memory.get_attack_results.call_args[1]
        assert call_kwargs["targeted_harm_categories"] == ["misinformation", "hate"]

    def test_injected_memory_bypasses_central_memory(self, _patch_memory):
        injected = MagicMock()
        injected.get_attack_results.return_value = [
            _make_result(eval_hash="a", outcome=AttackOutcome.SUCCESS),
        ]

        stats = compute_technique_stats(technique_eval_hashes=["a"], memory=injected)

        injected.get_attack_results.assert_called_once()
        _patch_memory.get_attack_results.assert_not_called()
        assert stats["a"].successes == 1


class TestComputeLabeledTechniqueStats:
    def test_counts_distinct_labeled_techniques(self, _patch_memory):
        label_name = "_adaptive_technique_id"
        _patch_memory.get_attack_results.return_value = [
            _make_labeled_result(
                label_name=label_name,
                technique_identifier="role-play-movie",
                outcome=AttackOutcome.SUCCESS,
            ),
            _make_labeled_result(
                label_name=label_name,
                technique_identifier="role-play-video",
                outcome=AttackOutcome.FAILURE,
            ),
        ]

        stats = compute_labeled_technique_stats(
            technique_identifiers=["role-play-movie", "role-play-video"],
            label_name=label_name,
        )

        assert stats["role-play-movie"].successes == 1
        assert stats["role-play-video"].failures == 1
        assert _patch_memory.get_attack_results.call_args.kwargs["labels"] == {
            label_name: ["role-play-movie", "role-play-video"]
        }

    def test_unlabeled_and_unrequested_results_are_ignored(self, _patch_memory):
        label_name = "_adaptive_technique_id"
        unlabeled = _make_result(eval_hash="shared", outcome=AttackOutcome.SUCCESS)
        _patch_memory.get_attack_results.return_value = [
            unlabeled,
            _make_labeled_result(
                label_name=label_name,
                technique_identifier="other",
                outcome=AttackOutcome.SUCCESS,
            ),
        ]

        stats = compute_labeled_technique_stats(
            technique_identifiers=["requested"],
            label_name=label_name,
        )

        assert stats == {}

    def test_merges_labeled_and_normal_scenario_history_without_double_counting(self, _patch_memory):
        label_name = "_adaptive_technique_id"
        labeled = _make_labeled_result(
            label_name=label_name,
            technique_identifier="factory-arm",
            outcome=AttackOutcome.SUCCESS,
        )
        labeled.atomic_attack_identifier = MagicMock(eval_hash="inner-attack-hash")
        normal = _make_result(eval_hash="full-technique-hash", outcome=AttackOutcome.FAILURE)
        _patch_memory.get_attack_results.side_effect = [
            [labeled],
            [labeled, normal],
        ]

        stats = compute_labeled_technique_stats(
            technique_identifiers=["factory-arm"],
            label_name=label_name,
            technique_eval_hashes_by_identifier={"factory-arm": "full-technique-hash"},
        )

        assert stats["factory-arm"].successes == 1
        assert stats["factory-arm"].failures == 1
        assert stats["factory-arm"].total_decided == 2
        assert _patch_memory.get_attack_results.call_args_list[1].kwargs["atomic_attack_eval_hashes"] == [
            "full-technique-hash"
        ]
