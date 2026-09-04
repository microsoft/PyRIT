# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import Message, MessagePiece, Score, ScoreStatus
from pyrit.score import (
    FloatScaleScorer,
    HarmHumanLabeledEntry,
    HarmScorerEvaluator,
    HarmScorerMetrics,
    HumanLabeledDataset,
    MetricsType,
    ObjectiveHumanLabeledEntry,
    ObjectiveScorerEvaluator,
    ObjectiveScorerMetrics,
    RegistryUpdateBehavior,
    ScorerEvaluator,
    TrueFalseScorer,
)


@pytest.fixture
def mock_harm_scorer():
    scorer = MagicMock(spec=FloatScaleScorer)
    scorer._memory = MagicMock(spec=MemoryInterface)
    scorer._memory.add_message_to_memory = MagicMock()
    scorer._memory.get_message_pieces.return_value = []
    # Create a mock identifier with a controllable hash property
    mock_identifier = MagicMock()
    mock_identifier.hash = "test_hash_456"
    mock_identifier.eval_hash = "test_hash_456"
    mock_identifier.system_prompt_template = "test_system_prompt"
    scorer.get_identifier = MagicMock(return_value=mock_identifier)
    return scorer


@pytest.fixture
def mock_objective_scorer():
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer._memory = MagicMock(spec=MemoryInterface)
    scorer._memory.add_message_to_memory = MagicMock()
    scorer._memory.get_message_pieces.return_value = []
    # Create a mock identifier with a controllable hash property
    mock_identifier = MagicMock()
    mock_identifier.hash = "test_hash_123"
    mock_identifier.eval_hash = "test_hash_123"
    mock_identifier.user_prompt_template = "test_user_prompt"
    scorer.get_identifier = MagicMock(return_value=mock_identifier)
    return scorer


def test_from_scorer_harm(mock_harm_scorer):
    evaluator = ScorerEvaluator.from_scorer(mock_harm_scorer, metrics_type=MetricsType.HARM)
    assert isinstance(evaluator, HarmScorerEvaluator)
    evaluator2 = ScorerEvaluator.from_scorer(mock_harm_scorer)
    assert isinstance(evaluator2, HarmScorerEvaluator)


def test_from_scorer_objective(mock_objective_scorer):
    evaluator = ScorerEvaluator.from_scorer(mock_objective_scorer, metrics_type=MetricsType.OBJECTIVE)
    assert isinstance(evaluator, ObjectiveScorerEvaluator)
    evaluator2 = ScorerEvaluator.from_scorer(mock_objective_scorer)
    assert isinstance(evaluator2, ObjectiveScorerEvaluator)


async def test_evaluate_dataset_async_harm(mock_harm_scorer):
    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value="test", original_value_data_type="text")])
    ]
    entry1 = HarmHumanLabeledEntry(responses, [0.1, 0.3], "hate_speech")
    entry2 = HarmHumanLabeledEntry(responses, [0.2, 0.6], "hate_speech")
    mock_dataset = HumanLabeledDataset(
        name="test_dataset",
        metrics_type=MetricsType.HARM,
        entries=[entry1, entry2],
        version="1.0",
        harm_definition="hate_speech.yaml",
        harm_definition_version="1.0",
    )
    # Patch scorer to return fixed scores
    entry_values = [
        MagicMock(get_value=lambda: 0.2, score_category=["hate_speech"]),
        MagicMock(get_value=lambda: 0.4, score_category=["hate_speech"]),
    ]
    evaluator = HarmScorerEvaluator(mock_harm_scorer)
    evaluator._score_responses_grouped_async = AsyncMock(return_value=[[score] for score in entry_values])
    metrics = await evaluator.evaluate_dataset_async(labeled_dataset=mock_dataset, num_scorer_trials=2)
    assert mock_harm_scorer._memory.add_message_to_memory.call_count == 2
    assert isinstance(metrics, HarmScorerMetrics)
    assert metrics.mean_absolute_error == 0.0
    assert metrics.mae_standard_error == 0.0


async def test_evaluate_dataset_async_objective(mock_objective_scorer):
    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value="test", original_value_data_type="text")])
    ]
    entry = ObjectiveHumanLabeledEntry(responses, [True], "Test objective")
    mock_dataset = HumanLabeledDataset(
        name="test_dataset", metrics_type=MetricsType.OBJECTIVE, entries=[entry], version="1.0"
    )
    # Patch scorer to return fixed scores
    evaluator = ObjectiveScorerEvaluator(mock_objective_scorer)
    evaluator._score_responses_grouped_async = AsyncMock(return_value=[[MagicMock(get_value=lambda: False)]])
    metrics = await evaluator.evaluate_dataset_async(labeled_dataset=mock_dataset, num_scorer_trials=2)
    assert mock_objective_scorer._memory.add_message_to_memory.call_count == 1
    assert isinstance(metrics, ObjectiveScorerMetrics)
    assert metrics.accuracy == 0.0
    assert metrics.accuracy_standard_error == 0.0


async def test_evaluate_dataset_async_excludes_undetermined_responses(mock_objective_scorer):
    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value=value, original_value_data_type="text")])
        for value in ["unknown", "known"]
    ]
    entries = [
        ObjectiveHumanLabeledEntry([response], [expected], "Test objective")
        for response, expected in zip(responses, [True, False], strict=True)
    ]
    dataset = HumanLabeledDataset(
        name="test_dataset", metrics_type=MetricsType.OBJECTIVE, entries=entries, version="1.0"
    )
    evaluator = ObjectiveScorerEvaluator(mock_objective_scorer)
    evaluator._score_responses_grouped_async = AsyncMock(
        return_value=[
            [Score(score_type="true_false", status=ScoreStatus.UNDETERMINED)],
            [Score(score_type="true_false", score_value="false")],
        ]
    )

    metrics = await evaluator.evaluate_dataset_async(labeled_dataset=dataset, num_scorer_trials=2)

    assert metrics.accuracy == 1.0
    assert metrics.trial_scores.shape == (2, 1)


async def test_evaluate_dataset_async_selects_category_before_filtering_undetermined(mock_harm_scorer):
    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value=value)]) for value in ["unknown", "known"]
    ]
    entries = [
        HarmHumanLabeledEntry([response], [expected], "hate_speech")
        for response, expected in zip(responses, [0.8, 0.2], strict=True)
    ]
    dataset = HumanLabeledDataset(
        name="test_dataset",
        metrics_type=MetricsType.HARM,
        entries=entries,
        version="1.0",
        harm_definition="hate_speech.yaml",
        harm_definition_version="1.0",
    )
    evaluator = HarmScorerEvaluator(mock_harm_scorer)
    evaluator._score_responses_grouped_async = AsyncMock(
        return_value=[
            [
                Score(score_type="float_scale", score_value="0.9", score_category=["violence"]),
                Score(
                    score_type="float_scale",
                    status=ScoreStatus.UNDETERMINED,
                    score_category=["hate_speech"],
                ),
            ],
            [
                Score(
                    score_type="float_scale",
                    status=ScoreStatus.UNDETERMINED,
                    score_category=["violence"],
                ),
                Score(score_type="float_scale", score_value="0.2", score_category=["hate_speech"]),
            ],
        ]
    )

    metrics = await evaluator.evaluate_dataset_async(labeled_dataset=dataset, num_scorer_trials=1)

    assert metrics.num_responses == 1
    assert metrics.mean_absolute_error == 0.0
    assert metrics.trial_scores.tolist() == [[0.2]]


async def test_evaluate_dataset_async_objective_returns_metrics(mock_objective_scorer):
    """Test that evaluate_dataset_async returns metrics without registry or file side effects."""
    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value="test", original_value_data_type="text")])
    ]
    entry = ObjectiveHumanLabeledEntry(responses, [True], "Test objective")
    mock_dataset = HumanLabeledDataset(
        name="test_dataset", metrics_type=MetricsType.OBJECTIVE, entries=[entry], version="1.0"
    )
    mock_objective_scorer.get_identifier = MagicMock(return_value=MagicMock(hash="test_hash"))
    evaluator = ObjectiveScorerEvaluator(mock_objective_scorer)
    evaluator._score_responses_grouped_async = AsyncMock(return_value=[[MagicMock(get_value=lambda: True)]])

    metrics = await evaluator.evaluate_dataset_async(labeled_dataset=mock_dataset, num_scorer_trials=1)

    # Verify metrics returned without registry writing
    assert metrics is not None
    assert isinstance(metrics, ObjectiveScorerMetrics)


def test_compute_objective_metrics_perfect_agreement(mock_objective_scorer):
    evaluator = ObjectiveScorerEvaluator(scorer=mock_objective_scorer)
    # 2 responses, 3 human scores each, all agree (all 1s), model also all 1s
    all_human_scores = np.array([[1, 1], [1, 1], [1, 1]])
    all_model_scores = np.array([[1, 1], [1, 1]])
    metrics = evaluator._compute_metrics(
        all_human_scores=all_human_scores, all_model_scores=all_model_scores, num_scorer_trials=2
    )
    assert metrics.accuracy == 1.0
    assert metrics.f1_score == 1.0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0


def test_compute_objective_metrics_partial_agreement(mock_objective_scorer):
    evaluator = ObjectiveScorerEvaluator(scorer=mock_objective_scorer)
    # 2 responses, 3 human scores each, mixed labels, model gets one right, one wrong
    all_human_scores = np.array([[1, 0], [1, 0], [0, 1]])  # gold: [1, 0]
    all_model_scores = np.array([[1, 1]])
    metrics = evaluator._compute_metrics(
        all_human_scores=all_human_scores, all_model_scores=all_model_scores, num_scorer_trials=1
    )
    # gold: [1, 0], model: [1, 1]
    # TP=1 (first), FP=1 (second), TN=0, FN=0
    assert metrics.accuracy == 0.5
    assert metrics.precision == 0.5
    assert metrics.recall == 1.0
    assert metrics.f1_score == pytest.approx(2 * 0.5 * 1.0 / (0.5 + 1.0))


def test_compute_harm_metrics_perfect_agreement(mock_harm_scorer):
    evaluator = HarmScorerEvaluator(mock_harm_scorer)
    # 2 responses, 3 human scores each, all agree, model matches exactly
    all_human_scores = np.array([[0.1, 0.2], [0.1, 0.2], [0.1, 0.2]])
    # 2 model trials
    all_model_scores = np.array([[0.1, 0.2], [0.1, 0.2]])
    # Patch krippendorff.krippendorff_alpha to return 1.0 for all calls
    metrics = evaluator._compute_metrics(
        all_human_scores=all_human_scores, all_model_scores=all_model_scores, num_scorer_trials=2
    )
    assert metrics.mean_absolute_error == 0.0
    assert metrics.mae_standard_error == 0.0
    # Perfect agreement: diff is all zeros, t-test guarded to avoid NaN propagation.
    assert metrics.t_statistic == 0.0
    assert metrics.p_value == 1.0
    assert metrics.krippendorff_alpha_combined == 1.0
    assert metrics.krippendorff_alpha_humans == 1.0
    assert metrics.krippendorff_alpha_model == 1.0


def test_compute_harm_metrics_partial_agreement(mock_harm_scorer):
    evaluator = HarmScorerEvaluator(scorer=mock_harm_scorer)
    # 2 responses, 3 human scores each, model is off by 0.1 for each (constant bias, zero variance)
    all_human_scores = np.array([[0.1, 0.2], [0.1, 0.2], [0.1, 0.2]])
    all_model_scores = np.array([[0.2, 0.3], [0.2, 0.3]])
    metrics = evaluator._compute_metrics(
        all_human_scores=all_human_scores, all_model_scores=all_model_scores, num_scorer_trials=2
    )
    assert np.isclose(metrics.mean_absolute_error, 0.1)
    # Constant non-zero diff has no within-sample variance: t-test undefined, reported as NaN.
    # MAE captures the bias magnitude.
    assert np.isnan(metrics.t_statistic)
    assert np.isnan(metrics.p_value)


def test_compute_harm_metrics_partial_agreement_with_variance(mock_harm_scorer):
    evaluator = HarmScorerEvaluator(scorer=mock_harm_scorer)
    # Model scores have variance across responses so ttest_1samp is well-defined.
    all_human_scores = np.array([[0.1, 0.5], [0.1, 0.5], [0.1, 0.5]])
    all_model_scores = np.array([[0.2, 0.3], [0.2, 0.3]])
    metrics = evaluator._compute_metrics(
        all_human_scores=all_human_scores, all_model_scores=all_model_scores, num_scorer_trials=2
    )
    # diff = [0.1, -0.2]; both t_statistic and p_value should be finite floats.
    assert np.isfinite(metrics.t_statistic)
    assert np.isfinite(metrics.p_value)
    assert 0.0 <= metrics.p_value <= 1.0


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_eval_hash")
def test_should_skip_evaluation_objective_found(mock_find, mock_objective_scorer, tmp_path):
    """Test skipping evaluation when existing objective metrics have sufficient trials."""
    evaluator = ObjectiveScorerEvaluator(scorer=mock_objective_scorer)
    result_file = tmp_path / "test_results.jsonl"

    # Hash is already set in the fixture mock_objective_scorer.identifier.hash = "test_hash_123"
    # Create expected metrics with same version and sufficient trials
    expected_metrics = ObjectiveScorerMetrics(
        num_responses=10,
        num_human_raters=3,
        accuracy=0.95,
        accuracy_standard_error=0.02,
        precision=0.96,
        recall=0.94,
        f1_score=0.95,
        num_scorer_trials=3,
        dataset_name="test_dataset",
        dataset_version="1.0",
    )
    mock_find.return_value = expected_metrics

    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",
        num_scorer_trials=3,
        harm_category=None,
        result_file_path=result_file,
    )

    assert should_skip is True
    assert result == expected_metrics
    mock_find.assert_called_once_with(
        file_path=result_file,
        eval_hash="test_hash_123",
    )


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_eval_hash")
def test_should_skip_evaluation_objective_not_found(mock_find, mock_objective_scorer, tmp_path):
    """Test when no existing objective metrics are found in registry."""
    evaluator = ObjectiveScorerEvaluator(scorer=mock_objective_scorer)
    result_file = tmp_path / "test_results.jsonl"

    mock_find.return_value = None

    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",
        num_scorer_trials=3,
        harm_category=None,
        result_file_path=result_file,
    )

    assert should_skip is False
    assert result is None
    mock_find.assert_called_once_with(
        file_path=result_file,
        eval_hash="test_hash_123",
    )


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_eval_hash")
def test_should_skip_evaluation_version_changed_runs_evaluation(mock_find, mock_objective_scorer, tmp_path):
    """Test that different dataset_version triggers re-evaluation (replace existing)."""
    evaluator = ObjectiveScorerEvaluator(scorer=mock_objective_scorer)
    result_file = tmp_path / "test_results.jsonl"

    # Metrics exist but with different dataset version
    existing_metrics = ObjectiveScorerMetrics(
        num_responses=10,
        num_human_raters=3,
        accuracy=0.95,
        accuracy_standard_error=0.02,
        precision=0.96,
        recall=0.94,
        f1_score=0.95,
        num_scorer_trials=3,
        dataset_name="test_dataset",
        dataset_version="2.0",  # Different version
    )
    mock_find.return_value = existing_metrics

    # When version differs, should NOT skip (run and replace)
    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",  # Looking for version 1.0
        num_scorer_trials=3,
        harm_category=None,
        result_file_path=result_file,
    )

    assert should_skip is False
    assert result is None


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_eval_hash")
def test_should_skip_evaluation_fewer_trials_requested_skips(mock_find, mock_objective_scorer, tmp_path):
    """Test that requesting fewer trials than existing skips evaluation."""
    evaluator = ObjectiveScorerEvaluator(scorer=mock_objective_scorer)
    result_file = tmp_path / "test_results.jsonl"

    # Metrics exist with more trials than requested
    existing_metrics = ObjectiveScorerMetrics(
        num_responses=10,
        num_human_raters=3,
        accuracy=0.95,
        accuracy_standard_error=0.02,
        precision=0.96,
        recall=0.94,
        f1_score=0.95,
        num_scorer_trials=5,  # Existing has 5 trials
        dataset_name="test_dataset",
        dataset_version="1.0",
    )
    mock_find.return_value = existing_metrics

    # Requesting only 3 trials - should skip since existing has more
    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",
        num_scorer_trials=3,  # Requesting fewer trials
        harm_category=None,
        result_file_path=result_file,
    )

    assert should_skip is True
    assert result == existing_metrics


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_eval_hash")
def test_should_skip_evaluation_more_trials_requested_runs(mock_find, mock_objective_scorer, tmp_path):
    """Test that requesting more trials than existing triggers re-evaluation."""
    evaluator = ObjectiveScorerEvaluator(scorer=mock_objective_scorer)
    result_file = tmp_path / "test_results.jsonl"

    # Metrics exist with fewer trials than requested
    existing_metrics = ObjectiveScorerMetrics(
        num_responses=10,
        num_human_raters=3,
        accuracy=0.95,
        accuracy_standard_error=0.02,
        precision=0.96,
        recall=0.94,
        f1_score=0.95,
        num_scorer_trials=2,  # Existing has only 2 trials
        dataset_name="test_dataset",
        dataset_version="1.0",
    )
    mock_find.return_value = existing_metrics

    # Requesting 5 trials - should NOT skip (run and replace)
    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",
        num_scorer_trials=5,  # Requesting more trials
        harm_category=None,
        result_file_path=result_file,
    )

    assert should_skip is False
    assert result is None


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_harm_metrics_by_eval_hash")
def test_should_skip_evaluation_harm_found(mock_find, mock_harm_scorer, tmp_path):
    """Test skipping evaluation when existing harm metrics have sufficient trials."""
    evaluator = HarmScorerEvaluator(scorer=mock_harm_scorer)
    result_file = tmp_path / "test_results.jsonl"

    # Create expected harm metrics
    expected_metrics = HarmScorerMetrics(
        num_responses=15,
        num_human_raters=4,
        mean_absolute_error=0.05,
        mae_standard_error=0.01,
        t_statistic=1.5,
        p_value=0.15,
        krippendorff_alpha_combined=0.85,
        krippendorff_alpha_humans=0.88,
        krippendorff_alpha_model=0.82,
        num_scorer_trials=3,
        dataset_name="harm_dataset",
        dataset_version="1.0",
        harm_category="hate_speech",
    )
    mock_find.return_value = expected_metrics

    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",
        num_scorer_trials=3,
        harm_category="hate_speech",
        result_file_path=result_file,
    )

    assert should_skip is True
    assert result == expected_metrics
    mock_find.assert_called_once_with(
        eval_hash="test_hash_456",
        file_path=result_file,
    )


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_harm_metrics_by_eval_hash")
def test_should_skip_evaluation_harm_missing_category(mock_find, mock_harm_scorer, tmp_path):
    """Test that missing harm_category returns should not skip."""
    evaluator = HarmScorerEvaluator(scorer=mock_harm_scorer)
    result_file = tmp_path / "test_results.jsonl"

    # No harm_category provided
    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",
        num_scorer_trials=3,
        harm_category=None,
        result_file_path=result_file,
    )

    assert should_skip is False
    assert result is None
    mock_find.assert_not_called()


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_eval_hash")
def test_should_skip_evaluation_exception_handling(mock_find, mock_objective_scorer, tmp_path):
    """Test that exceptions are caught and returns (False, None)."""
    evaluator = ObjectiveScorerEvaluator(scorer=mock_objective_scorer)
    result_file = tmp_path / "test_results.jsonl"

    # Make get_identifier() raise an exception
    mock_objective_scorer.get_identifier = MagicMock(side_effect=Exception("Identifier computation failed"))

    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",
        num_scorer_trials=3,
        harm_category=None,
        result_file_path=result_file,
    )

    assert should_skip is False
    assert result is None
    mock_find.assert_not_called()

    # Restore get_identifier for other tests
    mock_id = MagicMock()
    mock_id.hash = "test_hash_123"
    mock_id.eval_hash = "test_hash_123"
    mock_objective_scorer.get_identifier = MagicMock(return_value=mock_id)


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_harm_metrics_by_eval_hash")
def test_should_skip_evaluation_harm_definition_version_changed_runs_evaluation(mock_find, mock_harm_scorer, tmp_path):
    """Test that harm_definition_version change triggers re-evaluation."""
    evaluator = HarmScorerEvaluator(scorer=mock_harm_scorer)
    result_file = tmp_path / "test_results.jsonl"

    # Create existing metrics with older harm_definition_version
    existing_metrics = HarmScorerMetrics(
        num_responses=15,
        num_human_raters=4,
        mean_absolute_error=0.05,
        mae_standard_error=0.01,
        t_statistic=1.5,
        p_value=0.15,
        krippendorff_alpha_combined=0.85,
        krippendorff_alpha_humans=0.88,
        krippendorff_alpha_model=0.82,
        num_scorer_trials=3,
        dataset_name="harm_dataset",
        dataset_version="1.0",
        harm_category="hate_speech",
        harm_definition="hate_speech.yaml",
        harm_definition_version="1.0",
    )
    mock_find.return_value = existing_metrics

    # Request evaluation with newer harm_definition_version
    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",
        harm_definition_version="2.0",  # Different version
        num_scorer_trials=3,
        harm_category="hate_speech",
        result_file_path=result_file,
    )

    assert should_skip is False
    assert result is None


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_harm_metrics_by_eval_hash")
def test_should_skip_evaluation_harm_definition_version_same_skips(mock_find, mock_harm_scorer, tmp_path):
    """Test that matching harm_definition_version allows skip when other conditions met."""
    evaluator = HarmScorerEvaluator(scorer=mock_harm_scorer)
    result_file = tmp_path / "test_results.jsonl"

    # Create existing metrics with same harm_definition_version
    existing_metrics = HarmScorerMetrics(
        num_responses=15,
        num_human_raters=4,
        mean_absolute_error=0.05,
        mae_standard_error=0.01,
        t_statistic=1.5,
        p_value=0.15,
        krippendorff_alpha_combined=0.85,
        krippendorff_alpha_humans=0.88,
        krippendorff_alpha_model=0.82,
        num_scorer_trials=3,
        dataset_name="harm_dataset",
        dataset_version="1.0",
        harm_category="hate_speech",
        harm_definition="hate_speech.yaml",
        harm_definition_version="1.0",
    )
    mock_find.return_value = existing_metrics

    # Request evaluation with same harm_definition_version
    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",
        harm_definition_version="1.0",  # Same version
        num_scorer_trials=3,
        harm_category="hate_speech",
        result_file_path=result_file,
    )

    assert should_skip is True
    assert result == existing_metrics


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_harm_metrics_by_eval_hash")
def test_should_skip_evaluation_harm_definition_version_none_in_existing_runs_evaluation(
    mock_find, mock_harm_scorer, tmp_path
):
    """Test that if existing metrics has no harm_definition_version but request has one, re-run."""
    evaluator = HarmScorerEvaluator(scorer=mock_harm_scorer)
    result_file = tmp_path / "test_results.jsonl"

    # Create existing metrics without harm_definition_version (legacy)
    existing_metrics = HarmScorerMetrics(
        num_responses=15,
        num_human_raters=4,
        mean_absolute_error=0.05,
        mae_standard_error=0.01,
        t_statistic=1.5,
        p_value=0.15,
        krippendorff_alpha_combined=0.85,
        krippendorff_alpha_humans=0.88,
        krippendorff_alpha_model=0.82,
        num_scorer_trials=3,
        dataset_name="harm_dataset",
        dataset_version="1.0",
        harm_category="hate_speech",
        harm_definition="hate_speech.yaml",
        harm_definition_version=None,  # Legacy: no version
    )
    mock_find.return_value = existing_metrics

    # Request evaluation with harm_definition_version
    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",
        harm_definition_version="1.0",  # New: has version
        num_scorer_trials=3,
        harm_category="hate_speech",
        result_file_path=result_file,
    )

    assert should_skip is False
    assert result is None


async def test_evaluate_dataset_async_harm_passes_harm_definition_version(mock_harm_scorer):
    """Test that harm_definition_version from dataset is passed through to metrics."""
    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value="test", original_value_data_type="text")])
    ]
    entry = HarmHumanLabeledEntry(responses, [0.2, 0.4], "hate_speech")
    mock_dataset = HumanLabeledDataset(
        name="test_dataset",
        metrics_type=MetricsType.HARM,
        entries=[entry],
        version="1.0",
        harm_definition="hate_speech.yaml",
        harm_definition_version="1.0",
    )
    entry_values = [MagicMock(get_value=lambda: 0.3, score_category=["hate_speech"])]
    evaluator = HarmScorerEvaluator(mock_harm_scorer)
    evaluator._score_responses_grouped_async = AsyncMock(return_value=[[score] for score in entry_values])

    metrics = await evaluator.evaluate_dataset_async(labeled_dataset=mock_dataset, num_scorer_trials=1)

    assert isinstance(metrics, HarmScorerMetrics)
    assert metrics.harm_definition == "hate_speech.yaml"
    assert metrics.harm_definition_version == "1.0"
    assert metrics.dataset_version == "1.0"


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.HumanLabeledDataset.from_csv")
@patch("pyrit.score.scorer_evaluation.scorer_evaluator.SCORER_EVALS_PATH")
async def test_run_evaluation_async_combines_dataset_versions_with_duplicates(
    mock_evals_path, mock_from_csv, mock_harm_scorer, tmp_path
):
    """Test that run_evaluation_async concatenates all dataset versions including duplicates."""
    from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles

    # Create mock CSV files
    csv1 = tmp_path / "harm" / "file1.csv"
    csv2 = tmp_path / "harm" / "file2.csv"
    csv3 = tmp_path / "harm" / "file3.csv"
    (tmp_path / "harm").mkdir(parents=True)
    csv1.touch()
    csv2.touch()
    csv3.touch()

    mock_evals_path.__truediv__ = lambda self, x: tmp_path / x
    mock_evals_path.glob = lambda pattern: [csv1, csv2, csv3]

    # Create mock datasets with same versions (to test duplicate concatenation)
    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value="test", original_value_data_type="text")])
    ]
    entry = HarmHumanLabeledEntry(responses, [0.2], "hate_speech")

    def make_dataset(version, harm_def_version):
        return HumanLabeledDataset(
            name="test",
            metrics_type=MetricsType.HARM,
            entries=[entry],
            version=version,
            harm_definition="hate_speech.yaml",
            harm_definition_version=harm_def_version,
        )

    # All three files have dataset_version "1.0" - should concatenate to "1.0_1.0_1.0"
    # All have same harm_definition_version "1.0" - should stay as "1.0" (unique)
    mock_from_csv.side_effect = [
        make_dataset("1.0", "1.0"),
        make_dataset("1.0", "1.0"),
        make_dataset("1.0", "1.0"),
    ]

    evaluator = HarmScorerEvaluator(mock_harm_scorer)
    evaluator._score_responses_grouped_async = AsyncMock(
        return_value=[
            [MagicMock(get_value=lambda: 0.2, score_category=["hate_speech"])],
            [MagicMock(get_value=lambda: 0.2, score_category=["hate_speech"])],
            [MagicMock(get_value=lambda: 0.2, score_category=["hate_speech"])],
        ]
    )
    dataset_files = ScorerEvalDatasetFiles(
        human_labeled_datasets_files=["harm/*.csv"],
        result_file="harm/test_metrics.jsonl",
        harm_category="hate_speech",
    )

    # Mock validate to skip YAML file check (validation already happened per-CSV)
    with patch.object(HumanLabeledDataset, "validate"):
        metrics = await evaluator.run_evaluation_async(
            dataset_files=dataset_files,
            num_scorer_trials=1,
            update_registry_behavior=RegistryUpdateBehavior.NEVER_UPDATE,
        )

    assert metrics is not None
    # dataset_version includes duplicates
    assert metrics.dataset_version == "1.0_1.0_1.0"
    # harm_definition_version is unique (all same, so just "1.0")
    assert metrics.harm_definition_version == "1.0"


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.HumanLabeledDataset.from_csv")
@patch("pyrit.score.scorer_evaluation.scorer_evaluator.SCORER_EVALS_PATH")
async def test_run_evaluation_async_combines_mixed_dataset_versions(
    mock_evals_path, mock_from_csv, mock_harm_scorer, tmp_path
):
    """Test that run_evaluation_async concatenates mixed dataset versions in sorted file order."""
    from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles

    # Create mock CSV files (named to control sort order)
    csv1 = tmp_path / "harm" / "a_file.csv"
    csv2 = tmp_path / "harm" / "b_file.csv"
    (tmp_path / "harm").mkdir(parents=True)
    csv1.touch()
    csv2.touch()

    mock_evals_path.__truediv__ = lambda self, x: tmp_path / x
    mock_evals_path.glob = lambda pattern: [csv2, csv1]  # Return out of order to test sorting

    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value="test", original_value_data_type="text")])
    ]
    entry = HarmHumanLabeledEntry(responses, [0.2], "violence")

    def make_dataset(version, harm_def_version):
        return HumanLabeledDataset(
            name="test",
            metrics_type=MetricsType.HARM,
            entries=[entry],
            version=version,
            harm_definition="violence.yaml",
            harm_definition_version=harm_def_version,
        )

    # Files have different dataset versions but same harm_definition_version
    mock_from_csv.side_effect = [
        make_dataset("1.0", "1.0"),  # a_file.csv (first after sorting)
        make_dataset("2.0", "1.0"),  # b_file.csv (second after sorting)
    ]

    evaluator = HarmScorerEvaluator(mock_harm_scorer)
    evaluator._score_responses_grouped_async = AsyncMock(
        return_value=[
            [MagicMock(get_value=lambda: 0.2, score_category=["violence"])],
            [MagicMock(get_value=lambda: 0.2, score_category=["violence"])],
        ]
    )
    dataset_files = ScorerEvalDatasetFiles(
        human_labeled_datasets_files=["harm/*.csv"],
        result_file="harm/test_metrics.jsonl",
        harm_category="violence",
    )

    # Mock validate to skip YAML file check
    with patch.object(HumanLabeledDataset, "validate"):
        metrics = await evaluator.run_evaluation_async(
            dataset_files=dataset_files,
            num_scorer_trials=1,
            update_registry_behavior=RegistryUpdateBehavior.NEVER_UPDATE,
        )

    assert metrics is not None
    # Sorted by filename: a_file.csv (1.0) then b_file.csv (2.0)
    assert metrics.dataset_version == "1.0_2.0"
    # harm_definition_version is unique (both same)
    assert metrics.harm_definition_version == "1.0"


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.HumanLabeledDataset.from_csv")
@patch("pyrit.score.scorer_evaluation.scorer_evaluator.SCORER_EVALS_PATH")
async def test_run_evaluation_async_raises_on_mismatched_harm_definition_versions(
    mock_evals_path, mock_from_csv, mock_harm_scorer, tmp_path
):
    """Test that run_evaluation_async raises error when harm_definition_versions differ."""
    from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles

    csv1 = tmp_path / "harm" / "file1.csv"
    csv2 = tmp_path / "harm" / "file2.csv"
    (tmp_path / "harm").mkdir(parents=True)
    csv1.touch()
    csv2.touch()

    mock_evals_path.__truediv__ = lambda self, x: tmp_path / x
    mock_evals_path.glob = lambda pattern: [csv1, csv2]

    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value="test", original_value_data_type="text")])
    ]
    entry = HarmHumanLabeledEntry(responses, [0.2], "violence")

    def make_dataset(version, harm_def_version):
        return HumanLabeledDataset(
            name="test",
            metrics_type=MetricsType.HARM,
            entries=[entry],
            version=version,
            harm_definition="violence.yaml",
            harm_definition_version=harm_def_version,
        )

    # Files have DIFFERENT harm_definition_versions - should raise error
    mock_from_csv.side_effect = [
        make_dataset("1.0", "1.0"),
        make_dataset("1.0", "2.0"),  # Different harm_definition_version!
    ]

    evaluator = HarmScorerEvaluator(mock_harm_scorer)
    dataset_files = ScorerEvalDatasetFiles(
        human_labeled_datasets_files=["harm/*.csv"],
        result_file="harm/test_metrics.jsonl",
        harm_category="violence",
    )

    with pytest.raises(ValueError, match="All CSVs in a harm evaluation must use the same harm_definition_version"):
        await evaluator.run_evaluation_async(
            dataset_files=dataset_files,
            num_scorer_trials=1,
            update_registry_behavior=RegistryUpdateBehavior.NEVER_UPDATE,
        )


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.HumanLabeledDataset.from_csv")
@patch("pyrit.score.scorer_evaluation.scorer_evaluator.SCORER_EVALS_PATH")
async def test_run_evaluation_async_raises_when_harm_csv_missing_harm_definition(
    mock_evals_path, mock_from_csv, mock_harm_scorer, tmp_path
):
    """Test that run_evaluation_async raises when harm CSVs have no harm_definition header."""
    from pyrit.score.scorer_evaluation.scorer_evaluator import ScorerEvalDatasetFiles

    csv1 = tmp_path / "harm" / "file1.csv"
    (tmp_path / "harm").mkdir(parents=True)
    csv1.touch()

    mock_evals_path.__truediv__ = lambda self, x: tmp_path / x
    mock_evals_path.glob = lambda pattern: [csv1]

    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value="test", original_value_data_type="text")])
    ]
    entry = HarmHumanLabeledEntry(responses, [0.2], "violence")

    # Dataset with no harm_definition set
    mock_from_csv.return_value = HumanLabeledDataset(
        name="test",
        metrics_type=MetricsType.HARM,
        entries=[entry],
        version="1.0",
        harm_definition=None,
        harm_definition_version=None,
    )

    evaluator = HarmScorerEvaluator(mock_harm_scorer)
    dataset_files = ScorerEvalDatasetFiles(
        human_labeled_datasets_files=["harm/*.csv"],
        result_file="harm/test_metrics.jsonl",
        harm_category="violence",
    )

    with pytest.raises(ValueError, match="No harm_definition found in CSV headers"):
        await evaluator.run_evaluation_async(
            dataset_files=dataset_files,
            num_scorer_trials=1,
            update_registry_behavior=RegistryUpdateBehavior.NEVER_UPDATE,
        )


def test_should_skip_evaluation_returns_false_when_eval_hash_is_none(tmp_path):
    """Test that _should_skip_evaluation returns (False, None) when scorer eval_hash is None."""
    scorer = MagicMock(spec=TrueFalseScorer)
    mock_identifier = MagicMock()
    mock_identifier.eval_hash = None
    scorer.get_identifier = MagicMock(return_value=mock_identifier)

    evaluator = ObjectiveScorerEvaluator(scorer=scorer)
    result_file = tmp_path / "test_results.jsonl"

    should_skip, result = evaluator._should_skip_evaluation(
        dataset_version="1.0",
        num_scorer_trials=3,
        harm_category=None,
        result_file_path=result_file,
    )

    assert should_skip is False
    assert result is None


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.replace_evaluation_results")
def test_write_metrics_to_registry_returns_early_when_eval_hash_is_none(mock_replace, tmp_path):
    """Test that _write_metrics_to_registry returns early when scorer eval_hash is None."""
    scorer = MagicMock(spec=FloatScaleScorer)
    mock_identifier = MagicMock()
    mock_identifier.eval_hash = None
    scorer.get_identifier = MagicMock(return_value=mock_identifier)

    evaluator = HarmScorerEvaluator(scorer=scorer)
    result_file = tmp_path / "test_results.jsonl"

    metrics = MagicMock()
    evaluator._write_metrics_to_registry(metrics=metrics, result_file_path=result_file)

    mock_replace.assert_not_called()


class TestSelectEvaluationScore:
    """Cover how the evaluator picks the one score that matches a labeled response."""

    @staticmethod
    def _score(*, category: list[str] | None) -> Score:
        return Score(score_type="float_scale", score_value="0.5", score_category=category)

    def test_returns_none_when_the_scorer_returned_nothing(self):
        assert ScorerEvaluator._select_evaluation_score(scores=[], harm_category="hate_speech") is None

    def test_accepts_a_lone_score_that_names_no_category(self):
        score = self._score(category=None)
        assert ScorerEvaluator._select_evaluation_score(scores=[score], harm_category="hate_speech") is score

    def test_accepts_a_lone_score_when_no_harm_category_is_labeled(self):
        score = self._score(category=["violence"])
        assert ScorerEvaluator._select_evaluation_score(scores=[score], harm_category=None) is score

    def test_accepts_a_lone_score_that_matches_the_labeled_harm(self):
        score = self._score(category=["hate_speech"])
        assert ScorerEvaluator._select_evaluation_score(scores=[score], harm_category="hate_speech") is score

    def test_accepts_a_lone_score_with_an_alias_for_the_canonical_harm(self):
        score = self._score(category=["Sexual"])
        assert ScorerEvaluator._select_evaluation_score(scores=[score], harm_category="SEXUAL_CONTENT") is score

    def test_rejects_a_lone_score_that_names_a_different_harm(self):
        score = self._score(category=["violence"])
        with pytest.raises(ValueError, match="requires a score for harm category 'hate_speech'"):
            ScorerEvaluator._select_evaluation_score(scores=[score], harm_category="hate_speech")

    def test_picks_the_single_category_match_from_several_scores(self):
        match = self._score(category=["hate_speech"])
        other = self._score(category=["violence"])
        assert ScorerEvaluator._select_evaluation_score(scores=[other, match], harm_category="hate_speech") is match

    def test_picks_an_aliased_category_match_from_several_scores(self):
        match = self._score(category=["Sexual"])
        other = self._score(category=["Violence"])
        assert (
            ScorerEvaluator._select_evaluation_score(
                scores=[other, match],
                harm_category="SEXUAL_CONTENT",
            )
            is match
        )

    def test_does_not_match_two_unknown_categories_as_other(self):
        score = self._score(category=["custom_score_category"])
        with pytest.raises(ValueError, match="requires a score for harm category 'custom_dataset_category'"):
            ScorerEvaluator._select_evaluation_score(
                scores=[score],
                harm_category="custom_dataset_category",
            )

    def test_rejects_several_scores_with_no_category_match(self):
        scores = [self._score(category=["violence"]), self._score(category=["self_harm"])]
        with pytest.raises(ValueError, match="requires exactly one score per response"):
            ScorerEvaluator._select_evaluation_score(scores=scores, harm_category="hate_speech")
