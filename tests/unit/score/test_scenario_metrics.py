# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

from pyrit.models.scenario_result import ScenarioResult
from pyrit.score.scenario_metrics import get_scenario_metrics


def test_get_scenario_metrics_returns_none_without_scorer_identifier():
    scenario_result = MagicMock(spec=ScenarioResult)
    scenario_result.objective_scorer_identifier = None

    assert get_scenario_metrics(scenario_result) is None


def test_get_scenario_metrics_delegates_to_find_by_eval_hash():
    scenario_result = MagicMock(spec=ScenarioResult)
    scenario_result.objective_scorer_identifier = MagicMock()

    metrics = MagicMock()

    with (
        patch("pyrit.score.scenario_metrics.ScorerEvaluationIdentifier") as mock_eval_identifier,
        patch("pyrit.score.scenario_metrics.find_objective_metrics_by_eval_hash", return_value=metrics) as mock_find,
    ):
        mock_eval_identifier.return_value.eval_hash = "abc123"

        result = get_scenario_metrics(scenario_result)

    assert result is metrics
    mock_eval_identifier.assert_called_once_with(scenario_result.objective_scorer_identifier)
    mock_find.assert_called_once_with(eval_hash="abc123")


def test_get_scenario_metrics_returns_none_when_no_metrics_found():
    scenario_result = MagicMock(spec=ScenarioResult)
    scenario_result.objective_scorer_identifier = MagicMock()

    with (
        patch("pyrit.score.scenario_metrics.ScorerEvaluationIdentifier") as mock_eval_identifier,
        patch("pyrit.score.scenario_metrics.find_objective_metrics_by_eval_hash", return_value=None),
    ):
        mock_eval_identifier.return_value.eval_hash = "abc123"

        assert get_scenario_metrics(scenario_result) is None
