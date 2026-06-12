# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

import pytest

from pyrit.analytics.result_analysis import AttackStats
from pyrit.scenario.scenarios.adaptive.selectors import (
    EpsilonGreedyTechniqueSelector,
    SelectorScope,
)

TECHNIQUES = ["a", "b", "c", "d"]

_COMPUTE_PATH = "pyrit.scenario.scenarios.adaptive.selectors.epsilon_greedy.compute_technique_stats"


def _seeded_selector(*, epsilon: float = 0.0, random_seed: int = 0) -> EpsilonGreedyTechniqueSelector:
    return EpsilonGreedyTechniqueSelector(epsilon=epsilon, random_seed=random_seed)


def _empty_rates(*args, **kwargs) -> dict[str, AttackStats]:
    """Return empty stats (all techniques unseen)."""
    return {}


def _rates_with_winner(winner: str, *, successes: int = 5, failures: int = 0):
    """Return stats where one technique has a clear win record and others have failures."""

    def _compute(*args, **kwargs):
        stats = {}
        total = successes + failures
        stats[winner] = AttackStats(
            success_rate=successes / total if total else None,
            total_decided=total,
            successes=successes,
            failures=failures,
            undetermined=0,
            errors=0,
        )
        for t in TECHNIQUES:
            if t != winner:
                stats[t] = AttackStats(
                    success_rate=0.0,
                    total_decided=5,
                    successes=0,
                    failures=5,
                    undetermined=0,
                    errors=0,
                )
        return stats

    return _compute


class TestEpsilonGreedyTechniqueSelectorInit:
    def test_init_defaults(self):
        EpsilonGreedyTechniqueSelector()

    @pytest.mark.parametrize("bad_epsilon", [-0.1, 1.1, 2.0, -1.0])
    def test_init_rejects_out_of_range_epsilon(self, bad_epsilon):
        with pytest.raises(ValueError, match="epsilon"):
            EpsilonGreedyTechniqueSelector(epsilon=bad_epsilon)


class TestEpsilonGreedyTechniqueSelectorSelect:
    @patch(
        "pyrit.scenario.scenarios.adaptive.selectors.epsilon_greedy.compute_technique_stats",
        side_effect=_empty_rates,
    )
    async def test_select_empty_techniques_raises(self, _mock):
        selector = _seeded_selector()
        with pytest.raises(ValueError, match="technique_identifiers"):
            await selector.select_async(technique_identifiers=[], objective="obj")

    @patch(
        "pyrit.scenario.scenarios.adaptive.selectors.epsilon_greedy.compute_technique_stats",
        side_effect=_empty_rates,
    )
    async def test_select_all_unseen_ties_resolved_randomly(self, _mock):
        winners = set()
        for s in range(50):
            sel = _seeded_selector(random_seed=s)
            result = await sel.select_async(technique_identifiers=TECHNIQUES, objective="obj")
            winners.add(result[0])
        assert len(winners) > 1
        assert winners.issubset(set(TECHNIQUES))

    @patch(
        "pyrit.scenario.scenarios.adaptive.selectors.epsilon_greedy.compute_technique_stats",
        side_effect=_rates_with_winner("b"),
    )
    async def test_select_exploits_clear_winner(self, _mock):
        selector = _seeded_selector()
        for _ in range(20):
            result = await selector.select_async(technique_identifiers=TECHNIQUES, objective="obj")
            assert result[0] == "b"

    @patch(
        "pyrit.scenario.scenarios.adaptive.selectors.epsilon_greedy.compute_technique_stats",
        side_effect=_empty_rates,
    )
    async def test_select_epsilon_one_is_pure_random(self, _mock):
        selector = _seeded_selector(epsilon=1.0)
        picks = set()
        for i in range(200):
            result = await selector.select_async(technique_identifiers=TECHNIQUES, objective=f"obj-{i}")
            picks.add(result[0])
        assert picks == set(TECHNIQUES)

    @patch(
        "pyrit.scenario.scenarios.adaptive.selectors.epsilon_greedy.compute_technique_stats",
        side_effect=_empty_rates,
    )
    async def test_select_returns_multiple_techniques(self, _mock):
        selector = _seeded_selector()
        result = await selector.select_async(technique_identifiers=TECHNIQUES, objective="obj", num_top_techniques=3)
        assert len(result) == 3
        assert len(set(result)) == 3  # no duplicates

    @patch(
        "pyrit.scenario.scenarios.adaptive.selectors.epsilon_greedy.compute_technique_stats",
        side_effect=_empty_rates,
    )
    async def test_select_caps_at_available_techniques(self, _mock):
        selector = _seeded_selector()
        result = await selector.select_async(technique_identifiers=["a", "b"], objective="obj", num_top_techniques=5)
        assert len(result) == 2


class TestEpsilonGreedySelectorScope:
    @patch(_COMPUTE_PATH, side_effect=_empty_rates)
    async def test_default_scope_passes_none_scenario_result_id(self, mock_compute):
        selector = _seeded_selector()
        await selector.select_async(technique_identifiers=TECHNIQUES, objective="obj", scenario_result_id="run-1")

        # Default scope is all_runs(): the per-call scenario_result_id is dropped.
        assert mock_compute.call_args.kwargs["scenario_result_id"] is None

    @patch(_COMPUTE_PATH, side_effect=_empty_rates)
    async def test_current_run_scope_forwards_scenario_result_id(self, mock_compute):
        selector = EpsilonGreedyTechniqueSelector(epsilon=0.0, random_seed=0, scope=SelectorScope.current_run())
        await selector.select_async(technique_identifiers=TECHNIQUES, objective="obj", scenario_result_id="run-42")

        assert mock_compute.call_args.kwargs["scenario_result_id"] == "run-42"


class TestEpsilonGreedyEstimate:
    def test_estimate_unseen_is_one(self):
        assert EpsilonGreedyTechniqueSelector._estimate(technique="a", stats={}) == pytest.approx(1.0)

    def test_estimate_with_data(self):
        stats = {"a": AttackStats(success_rate=0.6, total_decided=5, successes=3, failures=2, undetermined=0, errors=0)}
        # (3 + 1) / (5 + 1) = 4/6 ≈ 0.6667
        assert EpsilonGreedyTechniqueSelector._estimate(technique="a", stats=stats) == pytest.approx(4 / 6)
