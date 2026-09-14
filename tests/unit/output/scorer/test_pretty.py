# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.models import ComponentIdentifier
from pyrit.output.scorer.pretty import PrettyScorerMemoryPrinter
from pyrit.score.scorer_evaluation.scorer_metrics import (
    HarmScorerMetrics,
    ObjectiveScorerMetrics,
)


def _make_scorer_identifier(
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


def _make_objective_metrics(**overrides) -> ObjectiveScorerMetrics:
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


def _make_harm_metrics(**overrides) -> HarmScorerMetrics:
    defaults = {
        "num_responses": 50,
        "num_human_raters": 2,
        "mean_absolute_error": 0.08,
        "mae_standard_error": 0.01,
        "t_statistic": 1.5,
        "p_value": 0.13,
        "krippendorff_alpha_combined": 0.85,
        "krippendorff_alpha_model": 0.82,
        "average_score_time_seconds": 0.8,
    }
    defaults.update(overrides)
    return HarmScorerMetrics(**defaults)


# --- __init__ tests ---


def test_init_default_values():
    printer = PrettyScorerMemoryPrinter()
    assert printer._indent == "  "
    assert printer._enable_colors is True


def test_init_custom_indent():
    printer = PrettyScorerMemoryPrinter(indent_size=4)
    assert printer._indent == "    "


def test_init_zero_indent():
    printer = PrettyScorerMemoryPrinter(indent_size=0)
    assert printer._indent == ""


def test_init_negative_indent_raises():
    with pytest.raises(ValueError, match="indent_size must be non-negative"):
        PrettyScorerMemoryPrinter(indent_size=-1)


# --- write_async (objective) tests ---


@patch("pyrit.models.ScorerEvaluationIdentifier")
@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash")
async def test_write_async_objective_with_metrics(mock_find, mock_eval_id_cls, capsys):
    printer = PrettyScorerMemoryPrinter(enable_colors=False)
    identifier = _make_scorer_identifier(class_name="MyScorer")

    mock_eval_id_cls.return_value = MagicMock(eval_hash="abc123")
    mock_find.return_value = _make_objective_metrics()

    await printer.write_async(scorer_identifier=identifier)
    output = capsys.readouterr().out

    assert "Scorer Information" in output
    assert "MyScorer" in output
    assert "Accuracy" in output
    assert "F1 Score" in output
    assert "Precision" in output
    assert "Recall" in output
    mock_find.assert_called_once_with(eval_hash="abc123")


@patch("pyrit.models.ScorerEvaluationIdentifier")
@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash")
async def test_write_async_objective_omits_optional_fields(mock_find, mock_eval_id_cls, capsys):
    printer = PrettyScorerMemoryPrinter(enable_colors=False)
    mock_eval_id_cls.return_value = MagicMock(eval_hash="x")
    mock_find.return_value = _make_objective_metrics(
        accuracy_standard_error=None,
        f1_score=None,
        precision=None,
        recall=None,
        average_score_time_seconds=None,
    )

    await printer.write_async(scorer_identifier=_make_scorer_identifier())
    output = capsys.readouterr().out
    assert "Accuracy" in output
    assert "F1 Score" not in output
    assert "Precision" not in output
    assert "Recall" not in output


@patch("pyrit.models.ScorerEvaluationIdentifier")
@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash")
async def test_write_async_objective_no_metrics(mock_find, mock_eval_id_cls, capsys):
    printer = PrettyScorerMemoryPrinter(enable_colors=False)
    mock_eval_id_cls.return_value = MagicMock(eval_hash="xyz")
    mock_find.return_value = None

    await printer.write_async(scorer_identifier=_make_scorer_identifier())
    output = capsys.readouterr().out
    assert "Official evaluation has not been run yet" in output


# --- write_async (harm) tests ---


@patch("pyrit.models.ScorerEvaluationIdentifier")
@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_harm_metrics_by_eval_hash")
async def test_write_async_harm_with_metrics(mock_find, mock_eval_id_cls, capsys):
    printer = PrettyScorerMemoryPrinter(enable_colors=False)
    mock_eval_id_cls.return_value = MagicMock(eval_hash="harm_hash")
    mock_find.return_value = _make_harm_metrics()

    await printer.write_async(scorer_identifier=_make_scorer_identifier(class_name="HarmScorer"), harm_category="hate")
    output = capsys.readouterr().out

    assert "HarmScorer" in output
    assert "Mean Absolute Error" in output
    assert "Krippendorff Alpha (Combined)" in output
    assert "Krippendorff Alpha (Model)" in output
    mock_find.assert_called_once_with(eval_hash="harm_hash", harm_category="hate")


@patch("pyrit.models.ScorerEvaluationIdentifier")
@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_harm_metrics_by_eval_hash")
async def test_write_async_harm_omits_optional_fields(mock_find, mock_eval_id_cls, capsys):
    printer = PrettyScorerMemoryPrinter(enable_colors=False)
    mock_eval_id_cls.return_value = MagicMock(eval_hash="x")
    mock_find.return_value = _make_harm_metrics(
        mae_standard_error=None,
        krippendorff_alpha_combined=None,
        krippendorff_alpha_model=None,
        average_score_time_seconds=None,
    )

    await printer.write_async(scorer_identifier=_make_scorer_identifier(), harm_category="violence")
    output = capsys.readouterr().out
    assert "Mean Absolute Error" in output
    assert "Krippendorff Alpha (Combined)" not in output


@patch("pyrit.models.ScorerEvaluationIdentifier")
@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_harm_metrics_by_eval_hash")
async def test_write_async_harm_no_metrics(mock_find, mock_eval_id_cls, capsys):
    printer = PrettyScorerMemoryPrinter(enable_colors=False)
    mock_eval_id_cls.return_value = MagicMock(eval_hash="no_data")
    mock_find.return_value = None

    await printer.write_async(scorer_identifier=_make_scorer_identifier(), harm_category="violence")
    output = capsys.readouterr().out
    assert "Official evaluation has not been run yet" in output


# --- write_async with composite scorer / display params / colors enabled ---


@patch("pyrit.models.ScorerEvaluationIdentifier")
@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash")
async def test_write_async_renders_compact_projected_component_tree(mock_find, mock_eval_id_cls, capsys):
    printer = PrettyScorerMemoryPrinter(enable_colors=False)
    mock_eval_id_cls.return_value = MagicMock(eval_hash="x")
    mock_find.return_value = _make_objective_metrics()

    target_id = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target",
        params={
            "endpoint": "https://example.com",
            "model_name": "gpt-4",
            "temperature": "0.0",
            "top_p": 0.9,
            "extra": "hidden",
        },
    )
    sub1 = _make_scorer_identifier(class_name="SubScorer1")
    sub2 = _make_scorer_identifier(class_name="SubScorer2")
    identifier = _make_scorer_identifier(
        class_name="CompositeScorer",
        params={
            "scorer_type": "likert",
            "score_aggregator": "mean",
            "system_prompt": "verbose " * 100 + "tail-marker",
        },
        children={"prompt_target": target_id, "sub_scorers": [sub1, sub2]},
    )

    await printer.write_async(scorer_identifier=identifier)
    output = capsys.readouterr().out

    assert "▸ sub_scorers (2 components)" in output
    assert "          • Component 1: SubScorer1\n" in output
    assert "          • Component 2: SubScorer2\n" in output
    assert "gpt-4" in output
    assert (
        "      • Scorer Type: CompositeScorer\n"
        "        Configuration:\n"
        "          score_aggregator=mean\n"
        "          scorer_type=likert\n"
        "          system_prompt=<811 chars>\n"
    ) in output
    assert "top_p=0.9" in output
    assert "system_prompt=<" in output
    assert "tail-marker" not in output
    assert "example.com" not in output
    assert "hidden" not in output


@patch("pyrit.models.ScorerEvaluationIdentifier")
@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash")
async def test_write_async_with_colors_enabled_emits_ansi_codes(mock_find, mock_eval_id_cls, capsys):
    printer = PrettyScorerMemoryPrinter(enable_colors=True)
    mock_eval_id_cls.return_value = MagicMock(eval_hash="x")
    # Mix metric values across quality bands so good/middle/bad color paths fire.
    mock_find.return_value = _make_objective_metrics(accuracy=0.95, f1_score=0.5, precision=0.8, recall=0.95)

    await printer.write_async(scorer_identifier=_make_scorer_identifier())
    output = capsys.readouterr().out
    assert "\x1b[" in output  # ANSI escape sequences present


@patch("pyrit.models.ScorerEvaluationIdentifier")
@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_harm_metrics_by_eval_hash")
async def test_write_async_harm_with_colors_covers_lower_is_better_bands(mock_find, mock_eval_id_cls, capsys):
    printer = PrettyScorerMemoryPrinter(enable_colors=True)
    mock_eval_id_cls.return_value = MagicMock(eval_hash="x")
    # Mix lower-is-better metric values across all quality bands (good / middle / bad).
    # MAE: good=0.1, bad=0.25; time: good=1.0, bad=3.0
    mock_find.return_value = _make_harm_metrics(mean_absolute_error=0.4, average_score_time_seconds=2.0)

    await printer.write_async(scorer_identifier=_make_scorer_identifier(), harm_category="violence")
    output = capsys.readouterr().out
    assert "\x1b[" in output
