# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from unittest.mock import MagicMock, patch

from pyrit.models import ComponentIdentifier
from pyrit.output.scorer.json import JsonScorerMemoryPrinter
from pyrit.score.scorer_evaluation.scorer_metrics import ObjectiveScorerMetrics


def _scorer_identifier(
    *,
    class_name: str = "TestScorer",
    params: dict | None = None,
    children: dict | None = None,
) -> ComponentIdentifier:
    return ComponentIdentifier(
        class_name=class_name,
        class_module="pyrit.score.test_scorer",
        params=params or {},
        children=children or {},
    )


def _objective_metrics(**overrides) -> ObjectiveScorerMetrics:
    defaults = {
        "num_responses": 100,
        "num_human_raters": 3,
        "accuracy": 0.92,
        "accuracy_standard_error": 0.02,
        "f1_score": 0.91,
        "precision": 0.93,
        "recall": 0.90,
        "average_score_time_seconds": 0.3,
    }
    defaults.update(overrides)
    return ObjectiveScorerMetrics(**defaults)


def _build(identifier: ComponentIdentifier, *, metrics=None) -> dict:
    printer = JsonScorerMemoryPrinter()
    with (
        patch("pyrit.models.ScorerEvaluationIdentifier") as mock_eval_id_cls,
        patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash") as mock_find,
    ):
        mock_eval_id_cls.return_value = MagicMock(eval_hash="hash")
        mock_find.return_value = metrics
        return printer.build(scorer_identifier=identifier)


def test_build_reports_class_name():
    payload = _build(_scorer_identifier(class_name="MyScorer"))
    assert payload["class_name"] == "MyScorer"


def test_build_includes_params_untruncated():
    long_value = "x" * 200
    payload = _build(_scorer_identifier(params={"threshold": 0.5, "prompt": long_value}))
    assert payload["params"]["threshold"] == 0.5
    # Pretty elides long values to "<N chars>"; JSON keeps them in full.
    assert payload["params"]["prompt"] == long_value


def test_build_omits_params_key_when_empty():
    payload = _build(_scorer_identifier(params={}))
    assert "params" not in payload


def test_build_recurses_into_children():
    child = _scorer_identifier(class_name="ChildScorer", params={"k": "v"})
    payload = _build(_scorer_identifier(children={"sub_scorer": child}))
    assert payload["children"]["sub_scorer"]["class_name"] == "ChildScorer"
    assert payload["children"]["sub_scorer"]["params"] == {"k": "v"}


def test_build_handles_child_lists():
    children = [_scorer_identifier(class_name="A"), _scorer_identifier(class_name="B")]
    payload = _build(_scorer_identifier(children={"scorers": children}))
    names = [c["class_name"] for c in payload["children"]["scorers"]]
    assert names == ["A", "B"]


def test_build_metrics_none_when_absent():
    payload = _build(_scorer_identifier(), metrics=None)
    assert payload["metrics"] is None


def test_build_metrics_serialized_when_present():
    payload = _build(_scorer_identifier(), metrics=_objective_metrics())
    assert payload["metrics"]["accuracy"] == 0.92
    assert payload["metrics"]["f1_score"] == 0.91
    # trial_scores (numpy) is dropped so the payload stays JSON-serializable.
    assert "trial_scores" not in payload["metrics"]


def test_build_metrics_maps_non_finite_to_null():
    payload = _build(
        _scorer_identifier(),
        metrics=_objective_metrics(accuracy=float("nan"), f1_score=float("inf"), recall=float("-inf")),
    )
    assert payload["metrics"]["accuracy"] is None
    assert payload["metrics"]["f1_score"] is None
    assert payload["metrics"]["recall"] is None
    # Finite values are untouched.
    assert payload["metrics"]["precision"] == 0.93


async def test_render_async_emits_valid_json_for_nan_metrics():
    printer = JsonScorerMemoryPrinter()
    with (
        patch("pyrit.models.ScorerEvaluationIdentifier") as mock_eval_id_cls,
        patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash") as mock_find,
    ):
        mock_eval_id_cls.return_value = MagicMock(eval_hash="hash")
        mock_find.return_value = _objective_metrics(accuracy=float("nan"))
        rendered = await printer.render_async(scorer_identifier=_scorer_identifier())

    # Bare NaN/Infinity tokens are invalid JSON; they must not appear in the output.
    assert "NaN" not in rendered
    assert "Infinity" not in rendered
    assert json.loads(rendered)["metrics"]["accuracy"] is None


async def test_render_async_returns_valid_json():
    printer = JsonScorerMemoryPrinter()
    with (
        patch("pyrit.models.ScorerEvaluationIdentifier") as mock_eval_id_cls,
        patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash") as mock_find,
    ):
        mock_eval_id_cls.return_value = MagicMock(eval_hash="hash")
        mock_find.return_value = _objective_metrics()
        rendered = await printer.render_async(scorer_identifier=_scorer_identifier(class_name="MyScorer"))

    payload = json.loads(rendered)
    assert payload["class_name"] == "MyScorer"
    assert payload["metrics"]["accuracy"] == 0.92


def test_build_harm_category_fetches_harm_metrics():
    printer = JsonScorerMemoryPrinter()
    with (
        patch("pyrit.models.ScorerEvaluationIdentifier") as mock_eval_id_cls,
        patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_harm_metrics_by_eval_hash") as mock_find,
    ):
        mock_eval_id_cls.return_value = MagicMock(eval_hash="hash")
        mock_find.return_value = None
        payload = printer.build(scorer_identifier=_scorer_identifier(class_name="HarmScorer"), harm_category="hate")

    mock_find.assert_called_once_with(eval_hash="hash", harm_category="hate")
    assert payload["class_name"] == "HarmScorer"
    assert payload["metrics"] is None
