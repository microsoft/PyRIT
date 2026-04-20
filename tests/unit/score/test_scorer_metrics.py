# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from pathlib import Path
from unittest.mock import patch

from pyrit.identifiers import ScorerIdentifier
from pyrit.score import (
    HarmScorerMetrics,
    ObjectiveScorerMetrics,
    ScorerMetricsWithIdentity,
)
from pyrit.score.scorer_evaluation.scorer_metrics_io import (
    get_all_harm_metrics,
    get_all_objective_metrics,
    replace_evaluation_results,
)


class TestScorerMetricsSerialization:
    """Tests for ScorerMetrics JSON serialization."""

    def test_harm_metrics_to_json_and_from_json(self, tmp_path):
        metrics = HarmScorerMetrics(
            num_responses=10,
            num_human_raters=3,
            mean_absolute_error=0.1,
            mae_standard_error=0.01,
            t_statistic=1.0,
            p_value=0.05,
            krippendorff_alpha_combined=0.8,
            krippendorff_alpha_humans=0.7,
            krippendorff_alpha_model=0.9,
        )
        json_str = metrics.to_json()
        data = json.loads(json_str)
        assert data["mean_absolute_error"] == 0.1

        # Save to file and reload
        file_path = tmp_path / "metrics.json"
        with open(file_path, "w") as f:
            f.write(json_str)
        loaded = HarmScorerMetrics.from_json(str(file_path))
        assert loaded == metrics

    def test_objective_metrics_to_json_and_from_json(self, tmp_path):
        metrics = ObjectiveScorerMetrics(
            num_responses=10,
            num_human_raters=3,
            accuracy=0.9,
            accuracy_standard_error=0.05,
            f1_score=0.8,
            precision=0.85,
            recall=0.75,
        )
        json_str = metrics.to_json()
        data = json.loads(json_str)
        assert data["accuracy"] == 0.9

        file_path = tmp_path / "metrics.json"
        with open(file_path, "w") as f:
            f.write(json_str)
        loaded = ObjectiveScorerMetrics.from_json(str(file_path))
        assert loaded == metrics


class TestScorerMetricsWithIdentity:
    """Tests for ScorerMetricsWithIdentity dataclass."""

    def test_creation_with_objective_metrics(self):
        scorer_id = ScorerIdentifier(
            class_name="TestScorer",
            class_module="test.module",
            class_description="Test scorer",
            identifier_type="instance",
        )
        metrics = ObjectiveScorerMetrics(
            num_responses=10,
            num_human_raters=2,
            accuracy=0.9,
            accuracy_standard_error=0.05,
            f1_score=0.85,
            precision=0.88,
            recall=0.82,
        )

        result = ScorerMetricsWithIdentity(
            scorer_identifier=scorer_id,
            metrics=metrics,
        )

        assert result.scorer_identifier.class_name == "TestScorer"
        assert result.metrics.accuracy == 0.9
        assert result.metrics.f1_score == 0.85

    def test_creation_with_harm_metrics(self):
        scorer_id = ScorerIdentifier(
            class_name="HarmScorer",
            class_module="test.module",
            class_description="Harm scorer",
            identifier_type="instance",
        )
        metrics = HarmScorerMetrics(
            num_responses=20,
            num_human_raters=3,
            mean_absolute_error=0.15,
            mae_standard_error=0.02,
            t_statistic=2.5,
            p_value=0.02,
            krippendorff_alpha_combined=0.75,
            harm_category="hate_speech",
        )

        result = ScorerMetricsWithIdentity(
            scorer_identifier=scorer_id,
            metrics=metrics,
        )

        assert result.scorer_identifier.class_name == "HarmScorer"
        assert result.metrics.mean_absolute_error == 0.15
        assert result.metrics.harm_category == "hate_speech"

    def test_repr(self):
        scorer_id = ScorerIdentifier(
            class_name="MyScorer",
            class_module="test.module",
            class_description="My scorer",
            identifier_type="instance",
        )
        metrics = ObjectiveScorerMetrics(
            num_responses=5,
            num_human_raters=1,
            accuracy=0.8,
            accuracy_standard_error=0.1,
            f1_score=0.75,
            precision=0.8,
            recall=0.7,
        )

        result = ScorerMetricsWithIdentity(
            scorer_identifier=scorer_id,
            metrics=metrics,
        )

        repr_str = repr(result)
        assert "MyScorer" in repr_str
        assert "ObjectiveScorerMetrics" in repr_str


class TestGetAllObjectiveMetrics:
    """Tests for the get_all_objective_metrics function."""

    def _create_objective_jsonl(self, path: Path) -> None:
        """Helper to create a test objective metrics JSONL file."""
        entries = [
            {
                "class_name": "SelfAskRefusalScorer",
                "class_module": "pyrit.score",
                "class_description": "Refusal scorer",
                "identifier_type": "instance",
                "system_prompt_template": "test prompt",
                "hash": "abc123",
                "metrics": {
                    "num_responses": 100,
                    "num_human_raters": 2,
                    "accuracy": 0.92,
                    "accuracy_standard_error": 0.03,
                    "f1_score": 0.88,
                    "precision": 0.90,
                    "recall": 0.86,
                    "num_scorer_trials": 3,
                },
            },
            {
                "class_name": "SelfAskTrueFalseScorer",
                "class_module": "pyrit.score",
                "class_description": "True/False scorer",
                "identifier_type": "instance",
                "system_prompt_template": "another prompt",
                "hash": "def456",
                "metrics": {
                    "num_responses": 50,
                    "num_human_raters": 3,
                    "accuracy": 0.85,
                    "accuracy_standard_error": 0.05,
                    "f1_score": 0.80,
                    "precision": 0.82,
                    "recall": 0.78,
                    "num_scorer_trials": 1,
                },
            },
        ]
        with open(path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def test_get_all_objective_metrics_loads_correctly(self, tmp_path):
        """Test loading objective metrics."""
        objective_dir = tmp_path / "objective"
        objective_dir.mkdir(parents=True, exist_ok=True)
        objective_file = objective_dir / "objective_achieved_metrics.jsonl"
        self._create_objective_jsonl(objective_file)

        with patch("pyrit.score.scorer_evaluation.scorer_metrics_io.SCORER_EVALS_PATH", tmp_path):
            results = get_all_objective_metrics()

        assert len(results) == 2
        assert all(isinstance(r.metrics, ObjectiveScorerMetrics) for r in results)

        # Check first entry
        first = results[0]
        assert first.scorer_identifier.class_name == "SelfAskRefusalScorer"
        assert first.metrics.accuracy == 0.92
        assert first.metrics.f1_score == 0.88

    def test_get_all_objective_metrics_custom_file_path(self, tmp_path):
        """Test loading from a custom file path."""
        custom_file = tmp_path / "custom_results.jsonl"
        self._create_objective_jsonl(custom_file)

        results = get_all_objective_metrics(file_path=custom_file)

        assert len(results) == 2
        assert all(isinstance(r.metrics, ObjectiveScorerMetrics) for r in results)

    def test_get_all_objective_metrics_empty_file(self, tmp_path):
        """Test handling of empty JSONL file."""
        objective_dir = tmp_path / "objective"
        objective_dir.mkdir(parents=True, exist_ok=True)
        empty_file = objective_dir / "objective_achieved_metrics.jsonl"
        empty_file.touch()

        with patch("pyrit.score.scorer_evaluation.scorer_metrics_io.SCORER_EVALS_PATH", tmp_path):
            results = get_all_objective_metrics()

        assert results == []

    def test_get_all_objective_metrics_missing_file(self, tmp_path):
        """Test handling of missing file (returns empty list)."""
        with patch("pyrit.score.scorer_evaluation.scorer_metrics_io.SCORER_EVALS_PATH", tmp_path):
            results = get_all_objective_metrics()

        assert results == []

    def test_get_all_objective_metrics_malformed_entry_skipped(self, tmp_path):
        """Test that malformed entries are skipped with warning."""
        objective_dir = tmp_path / "objective"
        objective_dir.mkdir(parents=True, exist_ok=True)
        file_path = objective_dir / "objective_achieved_metrics.jsonl"

        # Write one valid and one malformed entry
        with open(file_path, "w") as f:
            # Valid entry
            valid = {
                "class_name": "ValidScorer",
                "class_module": "test.module",
                "class_description": "A valid scorer for testing",
                "identifier_type": "instance",
                "metrics": {
                    "num_responses": 10,
                    "num_human_raters": 1,
                    "accuracy": 0.9,
                    "accuracy_standard_error": 0.05,
                    "f1_score": 0.85,
                    "precision": 0.88,
                    "recall": 0.82,
                },
            }
            f.write(json.dumps(valid) + "\n")
            # Malformed entry (missing required fields)
            malformed = {"class_name": "BadScorer", "metrics": {"accuracy": 0.5}}
            f.write(json.dumps(malformed) + "\n")

        with patch("pyrit.score.scorer_evaluation.scorer_metrics_io.SCORER_EVALS_PATH", tmp_path):
            results = get_all_objective_metrics()

        # Only the valid entry should be loaded
        assert len(results) == 1
        assert results[0].scorer_identifier.class_name == "ValidScorer"

    def test_get_all_objective_metrics_sortable_by_metric(self, tmp_path):
        """Test that results can be sorted by metrics attributes."""
        objective_dir = tmp_path / "objective"
        objective_dir.mkdir(parents=True, exist_ok=True)
        objective_file = objective_dir / "objective_achieved_metrics.jsonl"
        self._create_objective_jsonl(objective_file)

        with patch("pyrit.score.scorer_evaluation.scorer_metrics_io.SCORER_EVALS_PATH", tmp_path):
            results = get_all_objective_metrics()

        # Sort by f1_score descending
        sorted_results = sorted(results, key=lambda x: x.metrics.f1_score, reverse=True)

        assert sorted_results[0].metrics.f1_score == 0.88
        assert sorted_results[1].metrics.f1_score == 0.80

    def test_get_all_objective_metrics_scorer_identifier_reconstructed(self, tmp_path):
        """Test that ScorerIdentifier is properly reconstructed with all fields."""
        objective_dir = tmp_path / "objective"
        objective_dir.mkdir(parents=True, exist_ok=True)
        file_path = objective_dir / "objective_achieved_metrics.jsonl"

        entry = {
            "class_name": "ComplexScorer",
            "class_module": "pyrit.score.complex_scorer",
            "class_description": "A complex scorer for testing",
            "identifier_type": "instance",
            "system_prompt_template": "sha256:abcd1234",
            "user_prompt_template": "user template",
            "target_info": {"model": "gpt-4", "temperature": 0.7},
            "scorer_specific_params": {"threshold": 0.5},
            "hash": "fullhash123",
            "metrics": {
                "num_responses": 10,
                "num_human_raters": 1,
                "accuracy": 0.9,
                "accuracy_standard_error": 0.05,
                "f1_score": 0.85,
                "precision": 0.88,
                "recall": 0.82,
            },
        }
        with open(file_path, "w") as f:
            f.write(json.dumps(entry) + "\n")

        with patch("pyrit.score.scorer_evaluation.scorer_metrics_io.SCORER_EVALS_PATH", tmp_path):
            results = get_all_objective_metrics()

        assert len(results) == 1
        scorer_id = results[0].scorer_identifier

        assert scorer_id.class_name == "ComplexScorer"
        assert scorer_id.system_prompt_template == "sha256:abcd1234"
        assert scorer_id.user_prompt_template == "user template"
        assert scorer_id.target_info == {"model": "gpt-4", "temperature": 0.7}
        assert scorer_id.scorer_specific_params == {"threshold": 0.5}


class TestGetAllHarmMetrics:
    """Tests for the get_all_harm_metrics function."""

    def _create_harm_jsonl(self, path: Path) -> None:
        """Helper to create a test harm metrics JSONL file."""
        entries = [
            {
                "class_name": "SelfAskLikertScorer",
                "class_module": "pyrit.score",
                "class_description": "Likert scorer",
                "identifier_type": "instance",
                "system_prompt_template": "likert prompt",
                "hash": "harm123",
                "harm_category": "hate_speech",
                "metrics": {
                    "num_responses": 75,
                    "num_human_raters": 4,
                    "mean_absolute_error": 0.12,
                    "mae_standard_error": 0.02,
                    "t_statistic": 1.5,
                    "p_value": 0.14,
                    "krippendorff_alpha_combined": 0.78,
                    "harm_category": "hate_speech",
                    "num_scorer_trials": 2,
                },
            },
        ]
        with open(path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

    def test_get_all_harm_metrics_loads_correctly(self, tmp_path):
        """Test loading harm metrics."""
        harm_path = tmp_path / "harm"
        harm_path.mkdir()
        harm_file = harm_path / "hate_speech_metrics.jsonl"
        self._create_harm_jsonl(harm_file)

        with patch("pyrit.score.scorer_evaluation.scorer_metrics_io.SCORER_EVALS_PATH", tmp_path):
            results = get_all_harm_metrics("hate_speech")

        assert len(results) == 1
        assert isinstance(results[0].metrics, HarmScorerMetrics)
        assert results[0].scorer_identifier.class_name == "SelfAskLikertScorer"
        assert results[0].metrics.mean_absolute_error == 0.12
        assert results[0].metrics.harm_category == "hate_speech"

    def test_get_all_harm_metrics_different_category(self, tmp_path):
        """Test loading from a different harm category."""
        harm_path = tmp_path / "harm"
        harm_path.mkdir()
        violence_file = harm_path / "violence_metrics.jsonl"
        self._create_harm_jsonl(violence_file)

        with patch("pyrit.score.scorer_evaluation.scorer_metrics_io.SCORER_EVALS_PATH", tmp_path):
            results = get_all_harm_metrics("violence")

        assert len(results) == 1
        assert isinstance(results[0].metrics, HarmScorerMetrics)

    def test_get_all_harm_metrics_empty_file(self, tmp_path):
        """Test handling of empty JSONL file."""
        harm_path = tmp_path / "harm"
        harm_path.mkdir()
        empty_file = harm_path / "hate_speech_metrics.jsonl"
        empty_file.touch()

        with patch("pyrit.score.scorer_evaluation.scorer_metrics_io.SCORER_EVALS_PATH", tmp_path):
            results = get_all_harm_metrics("hate_speech")

        assert results == []

    def test_get_all_harm_metrics_missing_file(self, tmp_path):
        """Test handling of missing file (returns empty list)."""
        with patch("pyrit.score.scorer_evaluation.scorer_metrics_io.SCORER_EVALS_PATH", tmp_path):
            results = get_all_harm_metrics("nonexistent_category")

        assert results == []


class TestReplaceEvaluationResults:
    """Tests for replace_evaluation_results function."""

    def test_replace_adds_new_entry(self, tmp_path):
        """Test that replace_evaluation_results adds a new entry when none exists."""
        result_file = tmp_path / "test_results.jsonl"

        scorer_identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="test.module",
            class_description="Test scorer",
            identifier_type="instance",
            sub_identifier=[],
            target_info=None,
        )

        metrics = ObjectiveScorerMetrics(
            num_responses=10,
            num_human_raters=3,
            accuracy=0.9,
            accuracy_standard_error=0.05,
            f1_score=0.8,
            precision=0.85,
            recall=0.75,
            num_scorer_trials=3,
            dataset_version="1.0",
        )

        replace_evaluation_results(
            file_path=result_file,
            scorer_identifier=scorer_identifier,
            metrics=metrics,
        )

        # Verify the file contains the entry
        with open(result_file) as f:
            lines = f.readlines()

        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["hash"] == scorer_identifier.hash
        assert entry["metrics"]["accuracy"] == 0.9

    def test_replace_replaces_existing_entry(self, tmp_path):
        """Test that replace_evaluation_results replaces existing entry with same hash."""
        result_file = tmp_path / "test_results.jsonl"

        scorer_identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="test.module",
            class_description="Test scorer",
            identifier_type="instance",
            sub_identifier=[],
            target_info=None,
        )

        # Add initial entry
        initial_metrics = ObjectiveScorerMetrics(
            num_responses=10,
            num_human_raters=3,
            accuracy=0.7,
            accuracy_standard_error=0.05,
            f1_score=0.6,
            precision=0.65,
            recall=0.55,
            num_scorer_trials=1,
            dataset_version="1.0",
        )

        replace_evaluation_results(
            file_path=result_file,
            scorer_identifier=scorer_identifier,
            metrics=initial_metrics,
        )

        # Replace with updated metrics
        updated_metrics = ObjectiveScorerMetrics(
            num_responses=10,
            num_human_raters=3,
            accuracy=0.9,
            accuracy_standard_error=0.02,
            f1_score=0.85,
            precision=0.88,
            recall=0.82,
            num_scorer_trials=5,
            dataset_version="1.0",
        )

        replace_evaluation_results(
            file_path=result_file,
            scorer_identifier=scorer_identifier,
            metrics=updated_metrics,
        )

        # Verify only one entry exists with updated values
        with open(result_file) as f:
            lines = f.readlines()

        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["hash"] == scorer_identifier.hash
        assert entry["metrics"]["accuracy"] == 0.9
        assert entry["metrics"]["num_scorer_trials"] == 5

    def test_replace_preserves_other_entries(self, tmp_path):
        """Test that replace_evaluation_results preserves entries with different hashes."""
        result_file = tmp_path / "test_results.jsonl"

        # Add first scorer
        scorer1 = ScorerIdentifier(
            class_name="TestScorer1",
            class_module="test.module",
            class_description="Test scorer 1",
            identifier_type="instance",
            sub_identifier=[],
            target_info=None,
        )
        metrics1 = ObjectiveScorerMetrics(
            num_responses=10,
            num_human_raters=3,
            accuracy=0.8,
            accuracy_standard_error=0.05,
            f1_score=0.75,
            precision=0.78,
            recall=0.72,
            num_scorer_trials=3,
            dataset_version="1.0",
        )
        replace_evaluation_results(
            file_path=result_file,
            scorer_identifier=scorer1,
            metrics=metrics1,
        )

        # Add second scorer
        scorer2 = ScorerIdentifier(
            class_name="TestScorer2",
            class_module="test.module",
            class_description="Test scorer 2",
            identifier_type="instance",
            sub_identifier=[],
            target_info=None,
        )
        metrics2 = ObjectiveScorerMetrics(
            num_responses=15,
            num_human_raters=4,
            accuracy=0.85,
            accuracy_standard_error=0.03,
            f1_score=0.8,
            precision=0.82,
            recall=0.78,
            num_scorer_trials=3,
            dataset_version="1.0",
        )
        replace_evaluation_results(
            file_path=result_file,
            scorer_identifier=scorer2,
            metrics=metrics2,
        )

        # Now replace scorer1 with updated metrics
        updated_metrics1 = ObjectiveScorerMetrics(
            num_responses=10,
            num_human_raters=3,
            accuracy=0.95,
            accuracy_standard_error=0.02,
            f1_score=0.9,
            precision=0.92,
            recall=0.88,
            num_scorer_trials=5,
            dataset_version="2.0",
        )
        replace_evaluation_results(
            file_path=result_file,
            scorer_identifier=scorer1,
            metrics=updated_metrics1,
        )

        # Verify both entries exist, scorer1 updated, scorer2 preserved
        with open(result_file) as f:
            lines = f.readlines()

        assert len(lines) == 2
        entries = [json.loads(line) for line in lines]
        hashes = {e["hash"]: e for e in entries}

        assert scorer1.hash in hashes
        assert scorer2.hash in hashes
        assert hashes[scorer1.hash]["metrics"]["accuracy"] == 0.95
        assert hashes[scorer2.hash]["metrics"]["accuracy"] == 0.85
