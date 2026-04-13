# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest
from colorama import Fore, Style

from pyrit.identifiers import ComponentIdentifier
from pyrit.score.printer.console_scorer_printer import ConsoleScorerPrinter
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
    printer = ConsoleScorerPrinter()
    assert printer._indent == "  "
    assert printer._enable_colors is True


def test_init_custom_indent():
    printer = ConsoleScorerPrinter(indent_size=4)
    assert printer._indent == "    "


def test_init_zero_indent():
    printer = ConsoleScorerPrinter(indent_size=0)
    assert printer._indent == ""


def test_init_negative_indent_raises():
    with pytest.raises(ValueError, match="indent_size must be non-negative"):
        ConsoleScorerPrinter(indent_size=-1)


def test_init_colors_disabled():
    printer = ConsoleScorerPrinter(enable_colors=False)
    assert printer._enable_colors is False


# --- _print_colored tests ---


def test_print_colored_with_colors_enabled(capsys):
    printer = ConsoleScorerPrinter(enable_colors=True)
    printer._print_colored("hello", Fore.GREEN)
    captured = capsys.readouterr()
    assert "hello" in captured.out
    assert Style.RESET_ALL in captured.out


def test_print_colored_with_colors_disabled(capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    printer._print_colored("hello", Fore.GREEN)
    captured = capsys.readouterr()
    assert captured.out.strip() == "hello"
    assert Style.RESET_ALL not in captured.out


def test_print_colored_no_colors_arg(capsys):
    printer = ConsoleScorerPrinter(enable_colors=True)
    printer._print_colored("plain text")
    captured = capsys.readouterr()
    assert captured.out.strip() == "plain text"


# --- _get_quality_color tests ---


def test_quality_color_higher_is_better_good():
    printer = ConsoleScorerPrinter()
    color = printer._get_quality_color(0.95, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7)
    assert color == Fore.GREEN


def test_quality_color_higher_is_better_bad():
    printer = ConsoleScorerPrinter()
    color = printer._get_quality_color(0.5, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7)
    assert color == Fore.RED


def test_quality_color_higher_is_better_middle():
    printer = ConsoleScorerPrinter()
    color = printer._get_quality_color(0.8, higher_is_better=True, good_threshold=0.9, bad_threshold=0.7)
    assert color == Fore.CYAN


def test_quality_color_lower_is_better_good():
    printer = ConsoleScorerPrinter()
    color = printer._get_quality_color(0.05, higher_is_better=False, good_threshold=0.1, bad_threshold=0.25)
    assert color == Fore.GREEN


def test_quality_color_lower_is_better_bad():
    printer = ConsoleScorerPrinter()
    color = printer._get_quality_color(0.3, higher_is_better=False, good_threshold=0.1, bad_threshold=0.25)
    assert color == Fore.RED


def test_quality_color_lower_is_better_middle():
    printer = ConsoleScorerPrinter()
    color = printer._get_quality_color(0.15, higher_is_better=False, good_threshold=0.1, bad_threshold=0.25)
    assert color == Fore.CYAN


# --- _print_scorer_info tests ---


def test_print_scorer_info_basic(capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    identifier = _make_scorer_identifier(class_name="SelfAskScaleScorer")
    printer._print_scorer_info(identifier, indent_level=2)
    output = capsys.readouterr().out
    assert "SelfAskScaleScorer" in output


def test_print_scorer_info_with_display_params(capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    identifier = _make_scorer_identifier(
        class_name="TestScorer",
        params={"scorer_type": "likert", "score_aggregator": "mean", "hidden_param": "ignore"},
    )
    printer._print_scorer_info(identifier, indent_level=2)
    output = capsys.readouterr().out
    assert "scorer_type" in output
    assert "score_aggregator" in output
    assert "hidden_param" not in output


def test_print_scorer_info_with_prompt_target_child(capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    target_id = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target",
        params={"model_name": "gpt-4", "temperature": "0.0", "extra": "skip"},
    )
    identifier = _make_scorer_identifier(
        children={"prompt_target": target_id},
    )
    printer._print_scorer_info(identifier, indent_level=2)
    output = capsys.readouterr().out
    assert "gpt-4" in output
    assert "extra" not in output


def test_print_scorer_info_with_sub_scorers(capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    sub1 = _make_scorer_identifier(class_name="SubScorer1")
    sub2 = _make_scorer_identifier(class_name="SubScorer2")
    identifier = _make_scorer_identifier(
        class_name="CompositeScorer",
        children={"sub_scorers": [sub1, sub2]},
    )
    printer._print_scorer_info(identifier, indent_level=2)
    output = capsys.readouterr().out
    assert "Composite of 2 scorer(s)" in output
    assert "SubScorer1" in output
    assert "SubScorer2" in output


# --- _print_objective_metrics tests ---


def test_print_objective_metrics_none(capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    printer._print_objective_metrics(None)
    output = capsys.readouterr().out
    assert "Official evaluation has not been run yet" in output


def test_print_objective_metrics_full(capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    metrics = _make_objective_metrics()
    printer._print_objective_metrics(metrics)
    output = capsys.readouterr().out
    assert "Accuracy" in output
    assert "F1 Score" in output
    assert "Precision" in output
    assert "Recall" in output
    assert "Average Score Time" in output


def test_print_objective_metrics_optional_fields_none(capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    metrics = _make_objective_metrics(
        accuracy_standard_error=None,
        f1_score=None,
        precision=None,
        recall=None,
        average_score_time_seconds=None,
    )
    printer._print_objective_metrics(metrics)
    output = capsys.readouterr().out
    assert "Accuracy" in output
    assert "F1 Score" not in output
    assert "Precision" not in output
    assert "Recall" not in output
    assert "Average Score Time" not in output


# --- _print_harm_metrics tests ---


def test_print_harm_metrics_none(capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    printer._print_harm_metrics(None)
    output = capsys.readouterr().out
    assert "Official evaluation has not been run yet" in output


def test_print_harm_metrics_full(capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    metrics = _make_harm_metrics()
    printer._print_harm_metrics(metrics)
    output = capsys.readouterr().out
    assert "Mean Absolute Error" in output
    assert "Krippendorff Alpha (Combined)" in output
    assert "Krippendorff Alpha (Model)" in output
    assert "Average Score Time" in output


def test_print_harm_metrics_optional_fields_none(capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    metrics = _make_harm_metrics(
        mae_standard_error=None,
        krippendorff_alpha_combined=None,
        krippendorff_alpha_model=None,
        average_score_time_seconds=None,
    )
    printer._print_harm_metrics(metrics)
    output = capsys.readouterr().out
    assert "Mean Absolute Error" in output
    assert "MAE Std Error" not in output
    assert "Krippendorff Alpha (Combined)" not in output
    assert "Krippendorff Alpha (Model)" not in output
    assert "Average Score Time" not in output


# --- print_objective_scorer tests ---


@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash")
@patch("pyrit.identifiers.evaluation_identifier.ScorerEvaluationIdentifier")
def test_print_objective_scorer_with_metrics(mock_eval_id_cls, mock_find, capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    identifier = _make_scorer_identifier(class_name="MyScorer")
    metrics = _make_objective_metrics()

    mock_eval_instance = MagicMock()
    mock_eval_instance.eval_hash = "abc123"
    mock_eval_id_cls.return_value = mock_eval_instance
    mock_find.return_value = metrics

    printer.print_objective_scorer(scorer_identifier=identifier)
    output = capsys.readouterr().out

    assert "Scorer Information" in output
    assert "MyScorer" in output
    assert "Accuracy" in output
    mock_find.assert_called_once_with(eval_hash="abc123")


@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash")
@patch("pyrit.identifiers.evaluation_identifier.ScorerEvaluationIdentifier")
def test_print_objective_scorer_no_metrics(mock_eval_id_cls, mock_find, capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    identifier = _make_scorer_identifier()

    mock_eval_instance = MagicMock()
    mock_eval_instance.eval_hash = "xyz"
    mock_eval_id_cls.return_value = mock_eval_instance
    mock_find.return_value = None

    printer.print_objective_scorer(scorer_identifier=identifier)
    output = capsys.readouterr().out
    assert "Official evaluation has not been run yet" in output


# --- print_harm_scorer tests ---


@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_harm_metrics_by_eval_hash")
@patch("pyrit.identifiers.evaluation_identifier.ScorerEvaluationIdentifier")
def test_print_harm_scorer_with_metrics(mock_eval_id_cls, mock_find, capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    identifier = _make_scorer_identifier(class_name="HarmScorer")
    metrics = _make_harm_metrics()

    mock_eval_instance = MagicMock()
    mock_eval_instance.eval_hash = "harm_hash"
    mock_eval_id_cls.return_value = mock_eval_instance
    mock_find.return_value = metrics

    printer.print_harm_scorer(identifier, harm_category="hate_speech")
    output = capsys.readouterr().out

    assert "Scorer Information" in output
    assert "HarmScorer" in output
    assert "Mean Absolute Error" in output
    mock_find.assert_called_once_with(eval_hash="harm_hash", harm_category="hate_speech")


@patch("pyrit.score.scorer_evaluation.scorer_metrics_io.find_harm_metrics_by_eval_hash")
@patch("pyrit.identifiers.evaluation_identifier.ScorerEvaluationIdentifier")
def test_print_harm_scorer_no_metrics(mock_eval_id_cls, mock_find, capsys):
    printer = ConsoleScorerPrinter(enable_colors=False)
    identifier = _make_scorer_identifier()

    mock_eval_instance = MagicMock()
    mock_eval_instance.eval_hash = "no_data"
    mock_eval_id_cls.return_value = mock_eval_instance
    mock_find.return_value = None

    printer.print_harm_scorer(identifier, harm_category="violence")
    output = capsys.readouterr().out
    assert "Official evaluation has not been run yet" in output
