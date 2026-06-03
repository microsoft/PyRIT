# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scorer-evaluation glue for scenario results.

This lives in ``pyrit.score`` rather than on ``ScenarioResult`` because looking up
evaluation metrics requires the scoring layer; the data model must not depend on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.models.identifiers.evaluation_identifier import ScorerEvaluationIdentifier
from pyrit.score.scorer_evaluation.scorer_metrics_io import find_objective_metrics_by_eval_hash

if TYPE_CHECKING:
    from pyrit.models.scenario_result import ScenarioResult
    from pyrit.score.scorer_evaluation.scorer_metrics import ScorerMetrics


def get_scenario_metrics(scenario_result: ScenarioResult) -> ScorerMetrics | None:
    """
    Get the evaluation metrics for a scenario's objective scorer.

    Args:
        scenario_result (ScenarioResult): The scenario result whose objective scorer
            metrics should be looked up.

    Returns:
        ScorerMetrics | None: The evaluation metrics object, or None if the scenario
            has no objective scorer or no matching metrics are registered.

    """
    if not scenario_result.objective_scorer_identifier:
        return None

    eval_hash = ScorerEvaluationIdentifier(scenario_result.objective_scorer_identifier).eval_hash

    return find_objective_metrics_by_eval_hash(eval_hash=eval_hash)
