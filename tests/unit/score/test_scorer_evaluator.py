# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from pyrit.models import Message, MessagePiece
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
    scorer._memory = MagicMock()
    scorer._memory.add_message_to_memory = MagicMock()
    # Create a mock identifier with a controllable hash property
    mock_identifier = MagicMock()
    mock_identifier.hash = "test_hash_456"
    mock_identifier.system_prompt_template = "test_system_prompt"
    scorer.get_identifier = MagicMock(return_value=mock_identifier)
    return scorer


@pytest.fixture
def mock_objective_scorer():
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer._memory = MagicMock()
    scorer._memory.add_message_to_memory = MagicMock()
    # Create a mock identifier with a controllable hash property
    mock_identifier = MagicMock()
    mock_identifier.hash = "test_hash_123"
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


@pytest.mark.asyncio
async def test__run_evaluation_async_harm(mock_harm_scorer):
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
    entry_values = [MagicMock(get_value=lambda: 0.2), MagicMock(get_value=lambda: 0.4)]
    mock_harm_scorer.score_prompts_batch_async = AsyncMock(return_value=entry_values)
    evaluator = HarmScorerEvaluator(mock_harm_scorer)
    metrics = await evaluator._run_evaluation_async(labeled_dataset=mock_dataset, num_scorer_trials=2)
    assert mock_harm_scorer._memory.add_message_to_memory.call_count == 2
    assert isinstance(metrics, HarmScorerMetrics)
    assert metrics.mean_absolute_error == 0.0
    assert metrics.mae_standard_error == 0.0


@pytest.mark.asyncio
async def test__run_evaluation_async_objective(mock_objective_scorer):
    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value="test", original_value_data_type="text")])
    ]
    entry = ObjectiveHumanLabeledEntry(responses, [True], "Test objective")
    mock_dataset = HumanLabeledDataset(
        name="test_dataset", metrics_type=MetricsType.OBJECTIVE, entries=[entry], version="1.0"
    )
    # Patch scorer to return fixed scores
    mock_objective_scorer.score_prompts_batch_async = AsyncMock(return_value=[MagicMock(get_value=lambda: False)])
    evaluator = ObjectiveScorerEvaluator(mock_objective_scorer)
    metrics = await evaluator._run_evaluation_async(labeled_dataset=mock_dataset, num_scorer_trials=2)
    assert mock_objective_scorer._memory.add_message_to_memory.call_count == 1
    assert isinstance(metrics, ObjectiveScorerMetrics)
    assert metrics.accuracy == 0.0
    assert metrics.accuracy_standard_error == 0.0


@pytest.mark.asyncio
async def test__run_evaluation_async_objective_returns_metrics(mock_objective_scorer):
    """Test that _run_evaluation_async returns metrics without side effects."""
    responses = [
        Message(message_pieces=[MessagePiece(role="assistant", original_value="test", original_value_data_type="text")])
    ]
    entry = ObjectiveHumanLabeledEntry(responses, [True], "Test objective")
    mock_dataset = HumanLabeledDataset(
        name="test_dataset", metrics_type=MetricsType.OBJECTIVE, entries=[entry], version="1.0"
    )
    mock_objective_scorer.score_prompts_batch_async = AsyncMock(return_value=[MagicMock(get_value=lambda: True)])
    mock_objective_scorer.get_identifier = MagicMock(return_value=MagicMock(hash="test_hash"))
    evaluator = ObjectiveScorerEvaluator(mock_objective_scorer)

    metrics = await evaluator._run_evaluation_async(labeled_dataset=mock_dataset, num_scorer_trials=1)

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
    assert metrics.krippendorff_alpha_combined == 1.0
    assert metrics.krippendorff_alpha_humans == 1.0
    assert metrics.krippendorff_alpha_model == 1.0


def test_compute_harm_metrics_partial_agreement(mock_harm_scorer):
    evaluator = HarmScorerEvaluator(scorer=mock_harm_scorer)
    # 2 responses, 3 human scores each, model is off by 0.1 for each
    all_human_scores = np.array([[0.1, 0.2], [0.1, 0.2], [0.1, 0.2]])
    all_model_scores = np.array([[0.2, 0.3], [0.2, 0.3]])
    metrics = evaluator._compute_metrics(
        all_human_scores=all_human_scores, all_model_scores=all_model_scores, num_scorer_trials=2
    )
    assert np.isclose(metrics.mean_absolute_error, 0.1)


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_hash")
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
        hash="test_hash_123",
    )


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_hash")
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
        hash="test_hash_123",
    )


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_hash")
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


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_hash")
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


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_hash")
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


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_harm_metrics_by_hash")
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
        hash="test_hash_456",
        harm_category="hate_speech",
    )


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_harm_metrics_by_hash")
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


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_objective_metrics_by_hash")
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
    mock_identifier = MagicMock()
    mock_identifier.hash = "test_hash_123"
    mock_objective_scorer.get_identifier = MagicMock(return_value=mock_identifier)


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_harm_metrics_by_hash")
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


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_harm_metrics_by_hash")
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


@patch("pyrit.score.scorer_evaluation.scorer_evaluator.find_harm_metrics_by_hash")
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


@pytest.mark.asyncio
async def test__run_evaluation_async_harm_passes_harm_definition_version(mock_harm_scorer):
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
    entry_values = [MagicMock(get_value=lambda: 0.3)]
    mock_harm_scorer.score_prompts_batch_async = AsyncMock(return_value=entry_values)
    evaluator = HarmScorerEvaluator(mock_harm_scorer)

    metrics = await evaluator._run_evaluation_async(labeled_dataset=mock_dataset, num_scorer_trials=1)

    assert isinstance(metrics, HarmScorerMetrics)
    assert metrics.harm_definition == "hate_speech.yaml"
    assert metrics.harm_definition_version == "1.0"
    assert metrics.dataset_version == "1.0"


@pytest.mark.asyncio
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
        dataset = HumanLabeledDataset(
            name="test",
            metrics_type=MetricsType.HARM,
            entries=[entry],
            version=version,
            harm_definition="hate_speech.yaml",
            harm_definition_version=harm_def_version,
        )
        return dataset

    # All three files have dataset_version "1.0" - should concatenate to "1.0_1.0_1.0"
    # All have same harm_definition_version "1.0" - should stay as "1.0" (unique)
    mock_from_csv.side_effect = [
        make_dataset("1.0", "1.0"),
        make_dataset("1.0", "1.0"),
        make_dataset("1.0", "1.0"),
    ]

    mock_harm_scorer.score_prompts_batch_async = AsyncMock(
        return_value=[
            MagicMock(get_value=lambda: 0.2),
            MagicMock(get_value=lambda: 0.2),
            MagicMock(get_value=lambda: 0.2),
        ]
    )

    evaluator = HarmScorerEvaluator(mock_harm_scorer)
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


@pytest.mark.asyncio
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

    mock_harm_scorer.score_prompts_batch_async = AsyncMock(
        return_value=[MagicMock(get_value=lambda: 0.2), MagicMock(get_value=lambda: 0.2)]
    )

    evaluator = HarmScorerEvaluator(mock_harm_scorer)
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


@pytest.mark.asyncio
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
