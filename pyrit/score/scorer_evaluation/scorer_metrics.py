# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Optional, Type, TypeVar, Union

import numpy as np

from pyrit.common.utils import verify_and_resolve_path

if TYPE_CHECKING:
    from pyrit.identifiers import ScorerIdentifier
    from pyrit.models.harm_definition import HarmDefinition

T = TypeVar("T", bound="ScorerMetrics")
M = TypeVar("M", bound="ScorerMetrics")


@dataclass
class ScorerMetrics:
    """
    Base dataclass for storing scorer evaluation metrics.

    This class provides methods for serializing metrics to JSON and loading them from JSON files.

    Args:
        num_responses (int): Total number of responses evaluated.
        num_human_raters (int): Number of human raters who scored the responses.
        num_scorer_trials (int): Number of times the model scorer was run. Defaults to 1.
        dataset_name (str, optional): Name of the dataset used for evaluation.
        dataset_version (str, optional): Version of the dataset for reproducibility.
        trial_scores (np.ndarray, optional): Raw scores from each trial for debugging.
        average_score_time_seconds (float): Average time in seconds to score a single item. Defaults to 0.0.
    """

    num_responses: int
    num_human_raters: int
    num_scorer_trials: int = field(default=1, kw_only=True)
    dataset_name: Optional[str] = field(default=None, kw_only=True)
    dataset_version: Optional[str] = field(default=None, kw_only=True)
    trial_scores: Optional[np.ndarray] = field(default=None, kw_only=True)  # type: ignore[type-arg, unused-ignore]
    average_score_time_seconds: float = field(default=0.0, kw_only=True)

    def to_json(self) -> str:
        """
        Convert the metrics to a JSON string.

        Returns:
            str: The JSON string representation of the metrics.
        """
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls: Type[T], file_path: Union[str, Path]) -> T:
        """
        Load the metrics from a JSON file.

        Args:
            file_path (Union[str, Path]): The path to the JSON file.

        Returns:
            ScorerMetrics: An instance of ScorerMetrics with the loaded data.

        Raises:
            FileNotFoundError: If the specified file does not exist.
        """
        file_path = verify_and_resolve_path(file_path)
        with open(file_path, "r") as f:
            data = json.load(f)

        # Extract metrics from nested structure (always under "metrics" key in evaluation result files)
        metrics_data = data.get("metrics", data)

        # Filter out internal fields that shouldn't be passed to __init__
        # (e.g., _harm_definition_obj is a cached field with init=False)
        filtered_data = {k: v for k, v in metrics_data.items() if not k.startswith("_")}

        return cls(**filtered_data)


@dataclass
class HarmScorerMetrics(ScorerMetrics):
    """
    Metrics for evaluating a harm scorer against a HumanLabeledDataset.

    Args:
        mean_absolute_error (float): The mean absolute error between the model scores and the gold scores.
        mae_standard_error (float): The standard error of the mean absolute error. This can be used to calculate
            a confidence interval for the mean absolute error.
        t_statistic (float): The t-statistic for the one-sample t-test comparing model scores to human scores with a
            null hypothesis that the mean difference is 0. A high positive t-statistic (along with a low p-value)
            indicates that the model scores are typically higher than the human scores.
        p_value (float): The p-value for the one-sample t-test above. It represents the probability of obtaining a
            difference in means as extreme as the observed difference, assuming the null hypothesis is true.
        krippendorff_alpha_combined (float): Krippendorff's alpha for the reliability data, which includes both
            human and model scores. This measures the agreement between all the human raters and model scoring trials
            and ranges between -1.0 to 1.0 where 1.0 indicates perfect agreement, 0.0 indicates no agreement, and
            negative values indicate systematic disagreement.
        harm_category (str, optional): The harm category being evaluated (e.g., "hate_speech", "violence").
        harm_definition (str, optional): Path to the YAML file containing the harm definition (scale descriptions).
            Use get_harm_definition() to load the full HarmDefinition object.
        harm_definition_version (str, optional): Version of the harm definition YAML file that the human labels
            were created against. Used for reproducibility and to ensure scoring criteria consistency.
        krippendorff_alpha_humans (float, Optional): Krippendorff's alpha for human scores, if there are
            multiple human raters. This measures the agreement between human raters.
        krippendorff_alpha_model (float, Optional): Krippendorff's alpha for model scores, if there are
            multiple model scoring trials. This measures the agreement between model scoring trials.
    """

    mean_absolute_error: float
    mae_standard_error: float
    t_statistic: float
    p_value: float
    krippendorff_alpha_combined: float
    harm_category: Optional[str] = field(default=None, kw_only=True)
    harm_definition: Optional[str] = field(default=None, kw_only=True)
    harm_definition_version: Optional[str] = field(default=None, kw_only=True)
    krippendorff_alpha_humans: Optional[float] = None
    krippendorff_alpha_model: Optional[float] = None
    _harm_definition_obj: Optional["HarmDefinition"] = field(default=None, init=False, repr=False)

    def get_harm_definition(self) -> Optional["HarmDefinition"]:
        """
        Load and return the HarmDefinition object for this metrics instance.

        Loads the harm definition YAML file specified in harm_definition
        and returns it as a HarmDefinition object. The result is cached after
        the first load.

        Returns:
            HarmDefinition: The loaded harm definition object, or None if
                harm_definition is not set.

        Raises:
            FileNotFoundError: If the harm definition file does not exist.
            ValueError: If the harm definition file is invalid.
        """
        if not self.harm_definition:
            return None

        if self._harm_definition_obj is None:
            from pyrit.models.harm_definition import HarmDefinition

            self._harm_definition_obj = HarmDefinition.from_yaml(self.harm_definition)

        return self._harm_definition_obj


@dataclass
class ObjectiveScorerMetrics(ScorerMetrics):
    """
    Metrics for evaluating an objective scorer against a HumanLabeledDataset.

    Args:
        accuracy (float): The accuracy of the model scores when using the majority vote of
            human scores as the gold label.
        f1_score (float): The F1 score of the model scores, an indicator of performance of the
            LLM scorer in its alignment with human scores.
        precision (float): The precision of the model scores, an indicator of the model's accuracy
            in its positive predictions.
        recall (float): The recall of the model scores, an indicator of the model's ability to correctly
            identify positive labels.
        trial_scores (Optional[np.ndarray]): The raw scores from each trial. Shape is (num_trials, num_responses).
            Useful for debugging and analyzing scorer variance.
    """

    accuracy: float
    accuracy_standard_error: float
    f1_score: float
    precision: float
    recall: float


@dataclass
class ScorerMetricsWithIdentity(Generic[M]):
    """
    Wrapper that combines scorer metrics with the scorer's identity information.

    This class provides a clean interface for working with evaluation results,
    allowing access to both the scorer configuration and its performance metrics.

    Generic over the metrics type M, so:
    - ScorerMetricsWithIdentity[ObjectiveScorerMetrics] has metrics: ObjectiveScorerMetrics
    - ScorerMetricsWithIdentity[HarmScorerMetrics] has metrics: HarmScorerMetrics

    Args:
        scorer_identifier (ScorerIdentifier): The scorer's configuration identifier.
        metrics (M): The evaluation metrics (ObjectiveScorerMetrics or HarmScorerMetrics).
    """

    scorer_identifier: "ScorerIdentifier"
    metrics: M

    def __repr__(self) -> str:
        """Return a string representation of the ScorerMetricsWithIdentity."""
        metrics_type = type(self.metrics).__name__
        scorer_type = self.scorer_identifier.class_name
        return f"ScorerMetricsWithIdentity(scorer={scorer_type}, metrics_type={metrics_type})"
