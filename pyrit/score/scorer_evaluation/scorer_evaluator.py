# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, cast

import numpy as np
from scipy.stats import ttest_1samp

from pyrit.common.path import SCORER_EVALS_PATH
from pyrit.models import Message
from pyrit.score import Scorer
from pyrit.score.scorer_evaluation.human_labeled_dataset import (
    HarmHumanLabeledEntry,
    HumanLabeledDataset,
    ObjectiveHumanLabeledEntry,
)
from pyrit.score.scorer_evaluation.krippendorff import krippendorff_alpha
from pyrit.score.scorer_evaluation.metrics_type import (
    MetricsType,
    RegistryUpdateBehavior,
)
from pyrit.score.scorer_evaluation.scorer_metrics import (
    HarmScorerMetrics,
    ObjectiveScorerMetrics,
    ScorerMetrics,
)
from pyrit.score.scorer_evaluation.scorer_metrics_io import (
    find_harm_metrics_by_hash,
    find_objective_metrics_by_hash,
    replace_evaluation_results,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

logger = logging.getLogger(__name__)

# Standard column names for evaluation datasets
STANDARD_HUMAN_LABEL_COL = "human_score"
STANDARD_OBJECTIVE_COL = "objective"
STANDARD_HARM_COL = "harm_category"
STANDARD_ASSISTANT_RESPONSE_COL = "assistant_response"
STANDARD_DATA_TYPE_COL = "data_type"


@dataclass
class ScorerEvalDatasetFiles:
    """
    Configuration for evaluating a scorer on a set of dataset files.

    Maps input dataset files (via glob patterns) to an output result file.
    Multiple files matching the patterns will be concatenated before evaluation.

    Args:
        human_labeled_datasets_files (List[str]): List of glob patterns to match CSV files.
            Examples: ``["objective/*.csv"]``, ``["objective/hate_speech.csv", "objective/violence.csv"]``
        result_file (str): Name of the result file (stem used as dict key in results).
            Example: ``"objective_achieved_metrics.jsonl"``
        harm_category (Optional[str]): The harm category for harm scorers (e.g., "hate_speech", "violence").
            Required for harm evaluations, ignored for objective evaluations. Defaults to None.
    """

    human_labeled_datasets_files: List[str]
    result_file: str
    harm_category: Optional[str] = None


class ScorerEvaluator(abc.ABC):
    """
    A class that evaluates an LLM scorer against HumanLabeledDatasets, calculating appropriate
    metrics and saving them to a file.
    """

    # Subclasses must define the expected metrics type
    expected_metrics_type: MetricsType

    def __init__(self, scorer: Scorer):
        """
        Initialize the ScorerEvaluator with a scorer.

        Args:
            scorer (Scorer): The scorer to evaluate.
        """
        self.scorer = scorer

    @classmethod
    def from_scorer(cls, scorer: Scorer, metrics_type: Optional[MetricsType] = None) -> "ScorerEvaluator":
        """
        Create a ScorerEvaluator based on the type of scoring.

        Args:
            scorer (Scorer): The scorer to evaluate.
            metrics_type (MetricsType): The type of scoring, either HARM or OBJECTIVE.
                If not provided, it will default to OBJECTIVE for true/false scorers and HARM for all other
                scorers.

        Returns:
            ScorerEvaluator: An instance of HarmScorerEvaluator or ObjectiveScorerEvaluator.
        """
        if not metrics_type:
            metrics_type = MetricsType.OBJECTIVE if isinstance(scorer, TrueFalseScorer) else MetricsType.HARM

        _EVALUATOR_MAP = {MetricsType.HARM: HarmScorerEvaluator, MetricsType.OBJECTIVE: ObjectiveScorerEvaluator}

        evaluator = _EVALUATOR_MAP.get(metrics_type, HarmScorerEvaluator)
        return evaluator(scorer=scorer)

    async def run_evaluation_async(
        self,
        *,
        dataset_files: ScorerEvalDatasetFiles,
        num_scorer_trials: int = 3,
        update_registry_behavior: RegistryUpdateBehavior = RegistryUpdateBehavior.SKIP_IF_EXISTS,
        max_concurrency: int = 10,
    ) -> Optional[ScorerMetrics]:
        """
        Evaluate scorer using dataset files configuration.

        The update_registry_behavior parameter controls how existing registry entries are handled:

        - SKIP_IF_EXISTS (default): Check registry for existing results matching scorer config,
            dataset version, and num_scorer_trials. If found, return cached metrics.
            If not found, run evaluation and write to registry.
        - ALWAYS_UPDATE: Always run evaluation and overwrite any existing registry entry.
        - NEVER_UPDATE: Always run evaluation but never write to registry (for debugging).

        Args:
            dataset_files: ScorerEvalDatasetFiles configuration specifying glob patterns
                for input files and a result file name.
            num_scorer_trials: Number of scoring trials per response. Defaults to 3.
            update_registry_behavior: Controls how existing registry entries are handled.
                Defaults to RegistryUpdateBehavior.SKIP_IF_EXISTS.
            max_concurrency: Maximum number of concurrent scoring requests. Defaults to 10.

        Returns:
            ScorerMetrics if evaluation completed, None if no files found.

        Raises:
            ValueError: If harm_category is not specified for harm scorer evaluations.
        """
        metrics_type = MetricsType.OBJECTIVE if isinstance(self.scorer, TrueFalseScorer) else MetricsType.HARM

        # Validate harm_category for harm scorers
        if metrics_type == MetricsType.HARM:
            if dataset_files.harm_category is None:
                raise ValueError(
                    f"harm_category must be specified in ScorerEvalDatasetFiles for harm scorer evaluations. "
                    f"Missing for result_file: {dataset_files.result_file}"
                )

        # Collect all matching files
        csv_files: List[Path] = []
        for pattern in dataset_files.human_labeled_datasets_files:
            matched = list(SCORER_EVALS_PATH.glob(pattern))
            csv_files.extend(matched)

        if not csv_files:
            logger.warning(f"No files found for patterns {dataset_files.human_labeled_datasets_files}")
            return None

        # Load each CSV as a HumanLabeledDataset and combine entries
        # Sort csv_files for deterministic ordering when concatenating versions
        csv_files = sorted(csv_files)
        all_entries = []
        dataset_versions = []
        harm_definition_versions = set()
        for csv_file in csv_files:
            dataset = HumanLabeledDataset.from_csv(
                csv_path=csv_file,
                metrics_type=metrics_type,
            )
            all_entries.extend(dataset.entries)
            dataset_versions.append(dataset.version)
            if dataset.harm_definition_version:
                harm_definition_versions.add(dataset.harm_definition_version)

        # Concatenate all dataset versions (including duplicates) for full traceability
        # e.g., combining 3 CSVs all at v1.0 yields "1.0_1.0_1.0"
        combined_version = "_".join(dataset_versions)

        # Validate harm_definition_version consistency across all CSVs within a harm category.
        # Since each harm category evaluation uses a single harm definition YAML file,
        # all CSVs for that harm must have been labeled against the same version of that definition.
        if len(harm_definition_versions) > 1:
            raise ValueError(
                f"All CSVs in a harm evaluation must use the same harm_definition_version, "
                f"but found multiple versions: {sorted(harm_definition_versions)}."
            )
        combined_harm_definition_version = next(iter(harm_definition_versions)) if harm_definition_versions else None

        # Derive harm_definition from harm_category for harm datasets
        harm_definition = f"{dataset_files.harm_category}.yaml" if dataset_files.harm_category else None

        # Build dataset name from input CSV files
        dataset_name = "_".join(sorted(csv_file.name for csv_file in csv_files))

        # Create combined dataset
        combined_dataset = HumanLabeledDataset(
            entries=all_entries,
            metrics_type=metrics_type,
            name=dataset_name,
            version=combined_version,
            harm_definition=harm_definition,
            harm_definition_version=combined_harm_definition_version,
        )

        # Check for existing metrics only in SKIP_IF_EXISTS mode
        if update_registry_behavior == RegistryUpdateBehavior.SKIP_IF_EXISTS:
            should_skip, existing_metrics = self._should_skip_evaluation(
                dataset_version=combined_version,
                harm_definition_version=combined_harm_definition_version,
                num_scorer_trials=num_scorer_trials,
                harm_category=dataset_files.harm_category,
                result_file_path=SCORER_EVALS_PATH / dataset_files.result_file,
            )
            if should_skip and existing_metrics:
                logger.info(
                    f"Using existing evaluation results for {dataset_files.result_file}. "
                    f"(set update_registry_behavior=ALWAYS_UPDATE to force re-run)"
                )
                return existing_metrics

        # Run evaluation
        metrics = await self._run_evaluation_async(
            labeled_dataset=combined_dataset,
            num_scorer_trials=num_scorer_trials,
            max_concurrency=max_concurrency,
        )

        # Write to registry unless in NEVER_UPDATE mode
        if update_registry_behavior != RegistryUpdateBehavior.NEVER_UPDATE:
            self._write_metrics_to_registry(
                metrics=metrics,
                result_file_path=SCORER_EVALS_PATH / dataset_files.result_file,
            )

        return metrics

    def _should_skip_evaluation(
        self,
        *,
        dataset_version: str,
        harm_definition_version: Optional[str] = None,
        num_scorer_trials: int,
        harm_category: Optional[str] = None,
        result_file_path: Path,
    ) -> Tuple[bool, Optional[ScorerMetrics]]:
        """
        Determine whether to skip evaluation based on existing registry entries.

        Decision logic (only one entry per scorer hash is maintained):
        - If no existing entry: run evaluation
        - If existing dataset_version differs from requested: run and replace (assume newer dataset)
        - If existing harm_definition_version differs from requested: run and replace (scoring criteria changed)
        - If versions match and existing num_scorer_trials >= requested: skip (existing is sufficient)
        - If versions match and existing num_scorer_trials < requested: run and replace (higher fidelity)

        Args:
            dataset_version (str): The version of the dataset.
            harm_definition_version (Optional[str]): Version of the harm definition YAML. For harm evaluations.
            num_scorer_trials (int): Number of scorer trials requested.
            harm_category (Optional[str]): The harm category for harm scorers. Required for harm evaluations.
            result_file_path (Path): Path to the result file to search.

        Returns:
            Tuple[bool, Optional[ScorerMetrics]]: (should_skip, existing_metrics)
                - (True, metrics) if should skip and use existing metrics
                - (False, None) if should run evaluation
        """
        try:
            scorer_hash = self.scorer.get_identifier().hash

            # Determine if this is a harm or objective evaluation
            metrics_type = MetricsType.OBJECTIVE if isinstance(self.scorer, TrueFalseScorer) else MetricsType.HARM

            existing: Optional[ScorerMetrics] = None
            if metrics_type == MetricsType.HARM:
                if harm_category is None:
                    logger.warning("harm_category must be provided for harm scorer evaluations")
                    return (False, None)
                existing = find_harm_metrics_by_hash(
                    hash=scorer_hash,
                    harm_category=harm_category,
                )
            else:
                existing = find_objective_metrics_by_hash(
                    file_path=result_file_path,
                    hash=scorer_hash,
                )

            if not existing:
                logger.debug(f"No existing metrics found for hash {scorer_hash[:8]}...")
                return (False, None)

            # Check if dataset_version differs - if so, run and replace (assume newer dataset)
            if existing.dataset_version != dataset_version:
                logger.info(
                    f"Dataset version changed ({existing.dataset_version} -> {dataset_version}). "
                    f"Will re-run evaluation and replace existing entry."
                )
                return (False, None)

            # Check if harm_definition_version differs - if so, run and replace (scoring criteria changed)
            if harm_definition_version is not None and isinstance(existing, HarmScorerMetrics):
                if existing.harm_definition_version != harm_definition_version:
                    logger.info(
                        f"Harm definition version changed "
                        f"({existing.harm_definition_version} -> {harm_definition_version}). "
                        f"Will re-run evaluation and replace existing entry."
                    )
                    return (False, None)

            # Versions match - check num_scorer_trials
            if existing.num_scorer_trials >= num_scorer_trials:
                logger.info(
                    f"Found existing metrics with sufficient trials: "
                    f"dataset_version={dataset_version}, num_scorer_trials={existing.num_scorer_trials} "
                    f"(requested {num_scorer_trials}). Skipping evaluation."
                )
                return (True, existing)
            else:
                logger.info(
                    f"Existing metrics have fewer trials ({existing.num_scorer_trials} < {num_scorer_trials}). "
                    f"Will re-run evaluation with more trials and replace existing entry."
                )
                return (False, None)

        except Exception as e:
            logger.warning(f"Error checking for existing metrics: {e}")
            return (False, None)

    async def _run_evaluation_async(
        self,
        labeled_dataset: HumanLabeledDataset,
        num_scorer_trials: int = 1,
        max_concurrency: int = 10,
    ) -> ScorerMetrics:
        """
        Run the evaluation for the scorer/policy combination on the passed in HumanLabeledDataset.

        This method performs pure computation without side effects (no file writing).

        Args:
            labeled_dataset (HumanLabeledDataset): The HumanLabeledDataset to evaluate the scorer against.
            num_scorer_trials (int): The number of trials to run the scorer on all responses.
            max_concurrency (int): Maximum number of concurrent scoring requests. Defaults to 10.

        Returns:
            ScorerMetrics: The metrics for the scorer. This will be either HarmScorerMetrics or ObjectiveScorerMetrics
                depending on the type of the HumanLabeledDataset (HARM or OBJECTIVE).

        Raises:
            ValueError: If the labeled_dataset is invalid.
        """
        # Validate dataset and extract data
        assistant_responses, human_scores_list, objectives = self._validate_and_extract_data(labeled_dataset)

        # Transpose human scores so each row is a complete set of scores across all responses
        all_human_scores = np.array(human_scores_list).T

        # Run scoring trials and measure timing
        all_model_scores_list = []
        total_scoring_time = 0.0
        total_scored_items = 0
        for _ in range(num_scorer_trials):
            start_time = time.perf_counter()
            scores = await self.scorer.score_prompts_batch_async(
                messages=assistant_responses,
                objectives=objectives,
                batch_size=max_concurrency,
                infer_objective_from_request=True,
            )
            elapsed_time = time.perf_counter() - start_time
            total_scoring_time += elapsed_time
            total_scored_items += len(scores)
            score_values = [score.get_value() for score in scores]
            all_model_scores_list.append(score_values)
        all_model_scores = np.array(all_model_scores_list)

        # Calculate average time per scored item
        average_score_time = total_scoring_time / total_scored_items if total_scored_items > 0 else 0.0

        # Extract harm category if this is a harm dataset
        harm_category = None
        if labeled_dataset.metrics_type == MetricsType.HARM and labeled_dataset.entries:
            first_entry = labeled_dataset.entries[0]
            if isinstance(first_entry, HarmHumanLabeledEntry):
                harm_category = first_entry.harm_category
            if harm_category is None:
                raise ValueError(
                    "harm_category must be set in HarmHumanLabeledEntry for HARM datasets. "
                    "Ensure all entries have a valid harm_category."
                )

        # Compute metrics using subclass implementation
        metrics = self._compute_metrics(
            all_human_scores=all_human_scores,
            all_model_scores=all_model_scores,
            num_scorer_trials=num_scorer_trials,
            dataset_name=labeled_dataset.name,
            dataset_version=labeled_dataset.version,
            harm_category=harm_category,
            harm_definition=labeled_dataset.harm_definition,
            harm_definition_version=labeled_dataset.harm_definition_version,
        )

        # Include trial scores for debugging and future mismatch analysis
        # (not persisted to registry - use returned metrics object for detailed analysis)
        metrics.trial_scores = all_model_scores
        # Include average scoring time per item
        metrics.average_score_time_seconds = average_score_time

        return metrics

    @abc.abstractmethod
    def _validate_and_extract_data(
        self,
        labeled_dataset: HumanLabeledDataset,
    ) -> Tuple[List[Message], List[List[float]], Optional[List[str]]]:
        """
        Validate the dataset and extract data for evaluation.

        Args:
            labeled_dataset: The dataset to validate and extract from.

        Returns:
            Tuple of (assistant_responses, human_scores_list, objectives).
            objectives may be None for harm scoring.

        Raises:
            ValueError: If the dataset is invalid for this evaluator.
        """
        pass

    @abc.abstractmethod
    def _compute_metrics(
        self,
        *,
        all_human_scores: np.ndarray,  # type: ignore[type-arg, unused-ignore]
        all_model_scores: np.ndarray,  # type: ignore[type-arg, unused-ignore]
        num_scorer_trials: int,
        dataset_name: Optional[str] = None,
        dataset_version: Optional[str] = None,
        harm_category: Optional[str] = None,
        harm_definition: Optional[str] = None,
        harm_definition_version: Optional[str] = None,
    ) -> ScorerMetrics:
        """
        Compute evaluation metrics from human and model scores.

        Args:
            all_human_scores: Array of human scores, shape (num_raters, num_responses).
            all_model_scores: Array of model scores, shape (num_trials, num_responses).
            num_scorer_trials: Number of scoring trials that were performed.
            dataset_name: Name of the dataset being evaluated.
            dataset_version: Version of the dataset.
            harm_category: Harm category for harm metrics (ignored for objective metrics).
            harm_definition: Path to the harm definition YAML file (for harm metrics).
            harm_definition_version: Version of the harm definition YAML file (for harm metrics).

        Returns:
            ScorerMetrics subclass with computed metrics.
        """
        pass

    def _write_metrics_to_registry(
        self,
        *,
        metrics: ScorerMetrics,
        result_file_path: Path,
    ) -> None:
        """
        Write metrics to the evaluation registry file.

        Args:
            metrics (ScorerMetrics): The computed metrics.
            result_file_path (Path): The full path to the result file.
        """
        try:
            replace_evaluation_results(
                file_path=result_file_path,
                scorer_identifier=self.scorer.get_identifier(),
                metrics=metrics,
            )
        except Exception as e:
            logger.warning(f"Failed to write metrics to evaluation results: {e}")


class HarmScorerEvaluator(ScorerEvaluator):
    """
    A class that evaluates a harm scorer against HumanLabeledDatasets of type HARM.
    """

    expected_metrics_type = MetricsType.HARM

    def _validate_and_extract_data(
        self,
        labeled_dataset: HumanLabeledDataset,
    ) -> Tuple[List[Message], List[List[float]], Optional[List[str]]]:
        """
        Validate harm dataset and extract evaluation data.

        Args:
            labeled_dataset: The dataset to validate and extract from.

        Returns:
            Tuple of (assistant_responses, human_scores_list, None).
            objectives is None for harm scoring (uses infer_objective_from_request).

        Raises:
            ValueError: If dataset is not HARM type or has multiple harm categories.
        """
        if labeled_dataset.metrics_type != MetricsType.HARM:
            raise ValueError("The HumanLabeledDataset must be of type HARM to evaluate a harm scorer.")

        labeled_dataset.validate()

        assistant_responses: List[Message] = []
        human_scores_list: List[List[float]] = []

        for entry in labeled_dataset.entries:
            harm_entry = cast(HarmHumanLabeledEntry, entry)
            for message in harm_entry.conversation:
                self.scorer._memory.add_message_to_memory(request=message)
                assistant_responses.append(message)
            human_scores_list.append(harm_entry.human_scores)

        return assistant_responses, human_scores_list, None

    def _compute_metrics(
        self,
        *,
        all_human_scores: np.ndarray,  # type: ignore[type-arg, unused-ignore]
        all_model_scores: np.ndarray,  # type: ignore[type-arg, unused-ignore]
        num_scorer_trials: int,
        dataset_name: Optional[str] = None,
        dataset_version: Optional[str] = None,
        harm_category: Optional[str] = None,
        harm_definition: Optional[str] = None,
        harm_definition_version: Optional[str] = None,
    ) -> HarmScorerMetrics:
        reliability_data = np.concatenate((all_human_scores, all_model_scores))
        # Calculate the mean of human scores for each response, which is considered the gold label
        gold_scores = np.mean(all_human_scores, axis=0)
        mean_model_scores = np.mean(all_model_scores, axis=0)
        diff = mean_model_scores - gold_scores

        # Zero out tiny floating point noise
        diff[np.abs(diff) < 1e-10] = 0.0

        abs_error = np.abs(diff)
        t_statistic, p_value = cast(Tuple[float, float], ttest_1samp(diff, 0))

        num_responses = all_human_scores.shape[1]
        num_human_raters = all_human_scores.shape[0]

        krippendorff_alpha_humans = None
        if len(all_human_scores) > 1:
            krippendorff_alpha_humans = krippendorff_alpha(
                reliability_data=all_human_scores, level_of_measurement="ordinal"
            )

        krippendorff_alpha_model = None
        if len(all_model_scores) > 1:
            krippendorff_alpha_model = krippendorff_alpha(
                reliability_data=all_model_scores, level_of_measurement="ordinal"
            )

        return HarmScorerMetrics(
            num_responses=num_responses,
            num_human_raters=num_human_raters,
            mean_absolute_error=np.mean(abs_error),
            mae_standard_error=np.std(abs_error) / np.sqrt(len(abs_error)),
            t_statistic=t_statistic,
            p_value=p_value,
            krippendorff_alpha_combined=krippendorff_alpha(
                reliability_data=reliability_data, level_of_measurement="ordinal"
            ),
            krippendorff_alpha_humans=krippendorff_alpha_humans,
            krippendorff_alpha_model=krippendorff_alpha_model,
            num_scorer_trials=num_scorer_trials,
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            harm_category=harm_category,
            harm_definition=harm_definition,
            harm_definition_version=harm_definition_version,
        )


class ObjectiveScorerEvaluator(ScorerEvaluator):
    """
    A class that evaluates an objective scorer against HumanLabeledDatasets of type OBJECTIVE.
    """

    expected_metrics_type = MetricsType.OBJECTIVE

    def _validate_and_extract_data(
        self,
        labeled_dataset: HumanLabeledDataset,
    ) -> Tuple[List[Message], List[List[float]], Optional[List[str]]]:
        """
        Validate objective dataset and extract evaluation data.

        Args:
            labeled_dataset: The dataset to validate and extract from.

        Returns:
            Tuple of (assistant_responses, human_scores_list, objectives).

        Raises:
            ValueError: If dataset is not OBJECTIVE type or contains invalid entries.
        """
        if labeled_dataset.metrics_type != MetricsType.OBJECTIVE:
            raise ValueError("The HumanLabeledDataset must be of type OBJECTIVE to evaluate an objective scorer.")

        labeled_dataset.validate()

        assistant_responses: List[Message] = []
        human_scores_list: List[List[float]] = []
        objectives: List[str] = []

        for entry in labeled_dataset.entries:
            objective_entry = cast(ObjectiveHumanLabeledEntry, entry)
            for message in objective_entry.conversation:
                self.scorer._memory.add_message_to_memory(request=message)
                assistant_responses.append(message)
            human_scores_list.append([float(score) for score in objective_entry.human_scores])
            objectives.append(objective_entry.objective)

        return assistant_responses, human_scores_list, objectives

    def _compute_metrics(
        self,
        *,
        all_human_scores: np.ndarray,  # type: ignore[type-arg, unused-ignore]
        all_model_scores: np.ndarray,  # type: ignore[type-arg, unused-ignore]
        num_scorer_trials: int,
        dataset_name: Optional[str] = None,
        dataset_version: Optional[str] = None,
        harm_category: Optional[str] = None,
        harm_definition: Optional[str] = None,
        harm_definition_version: Optional[str] = None,
    ) -> ObjectiveScorerMetrics:
        # Calculate the majority vote of human scores for each response, which is considered the gold label.
        # If the vote is split, the resulting gold score will be 0 (i.e. False). Same logic is applied to model trials.
        gold_scores = np.round(np.mean(all_human_scores, axis=0))
        majority_model_scores = np.round(np.mean(all_model_scores, axis=0))

        true_positive = np.sum((gold_scores == 1) & (majority_model_scores == 1))
        false_positive = np.sum((gold_scores == 0) & (majority_model_scores == 1))
        true_negative = np.sum((gold_scores == 0) & (majority_model_scores == 0))
        false_negative = np.sum((gold_scores == 1) & (majority_model_scores == 0))
        accuracy = (true_positive + true_negative) / gold_scores.size
        precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) > 0 else 0.0
        recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) > 0 else 0.0
        f1_score = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        num_responses = all_human_scores.shape[1]
        num_human_raters = all_human_scores.shape[0]

        return ObjectiveScorerMetrics(
            num_responses=num_responses,
            num_human_raters=num_human_raters,
            accuracy=accuracy,
            accuracy_standard_error=np.sqrt(accuracy * (1 - accuracy) / gold_scores.size),
            precision=precision,
            recall=recall,
            f1_score=f1_score,
            num_scorer_trials=num_scorer_trials,
            dataset_name=dataset_name,
            dataset_version=dataset_version,
        )
