# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

import numpy as np

from pyrit.common.utils import verify_and_resolve_path

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.models import ComponentIdentifier
    from pyrit.models.harm_definition import HarmDefinition

T = TypeVar("T", bound="ScorerMetrics")
M = TypeVar("M", bound="ScorerMetrics")


def non_finite_to_none(value: Any) -> Any:
    """
    Return a copy of ``value`` in which every non-finite float has become ``None``.

    JSON (RFC 8259) has no number for ``NaN`` or ``Infinity``, but ``json.dumps`` writes them as
    the bare tokens ``NaN`` / ``Infinity`` by default. Python reads those back, so a metrics file
    written this way looks round-trip safe here while strict parsers reject the whole document:
    ``JSON.parse`` in JavaScript, and the .NET and Go standard JSON libraries both fail on it (jq
    accepts the tokens only as a documented extension). An undefined statistic therefore has to be
    serialized as ``null``, which is what this maps them to.

    Args:
        value (Any): A metrics value: a scalar, or a dict / list / tuple / numpy array of them.

    Returns:
        Any: The value with ``NaN`` and ``±inf`` replaced by ``None``. Numpy arrays become nested
            lists, which is the shape ``to_json`` already writes for them.
    """
    if isinstance(value, np.ndarray):
        return [non_finite_to_none(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: non_finite_to_none(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [non_finite_to_none(item) for item in value]
    return value


def _json_default(value: Any) -> Any:
    """
    Encode numpy arrays and scalars as plain JSON lists and numbers.

    Args:
        value (Any): The object ``json.dumps`` could not serialize on its own.

    Returns:
        Any: A JSON-serializable Python object.

    Raises:
        TypeError: If the value is not a numpy array or scalar.
    """
    if isinstance(value, (np.ndarray, np.generic)):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


@dataclass
class ScorerMetrics:
    """
    Base dataclass for storing scorer evaluation metrics.

    This class provides methods for serializing metrics to JSON strings (see
    ``to_json``) and loading them from JSON files on disk (see
    ``from_json_file``).

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
    dataset_name: str | None = field(default=None, kw_only=True)
    dataset_version: str | None = field(default=None, kw_only=True)
    trial_scores: np.ndarray | None = field(default=None, kw_only=True)
    average_score_time_seconds: float = field(default=0.0, kw_only=True)

    def to_json(self) -> str:
        """
        Serialize this metrics instance to a JSON string.

        This is the canonical serialization entry point for ``ScorerMetrics`` and its
        subclasses. Pair it with ``from_json_file`` (which reads a JSON file written
        from this string, optionally wrapped in a ``"metrics"`` key) for round-trip
        (de)serialization. Numpy values, such as the ``trial_scores`` array that
        ``ScorerEvaluator`` attaches to the metrics it returns, are written as nested
        JSON lists.

        Returns:
            str: The JSON string representation of the metrics. A statistic that is undefined
                (``NaN``) is written as ``null``, because JSON has no literal for it.

        Raises:
            TypeError: If a field holds a value that is neither JSON-serializable nor
                a numpy array or scalar.
        """
        return json.dumps(non_finite_to_none(asdict(self)), default=_json_default, allow_nan=False)

    @classmethod
    def from_json_file(cls: type[T], file_path: str | Path) -> T:
        """
        Load a metrics instance from a JSON file on disk.

        This is the canonical deserialization entry point for ``ScorerMetrics`` and its
        subclasses. It accepts a *file path* (string or ``Path``), not a JSON string —
        the loader opens the file, unwraps a top-level ``"metrics"`` key if present
        (as used by evaluation result files), filters out internal underscore-prefixed
        fields (e.g., cached ``init=False`` attributes), and reads a nested-list
        ``trial_scores`` back as a numpy array, whose dtype and shape follow the stored
        lists rather than the array that produced them.

        Args:
            file_path (str | Path): The path to the JSON file.

        Returns:
            ScorerMetrics: An instance of ScorerMetrics (or subclass) with the loaded data.

        Raises:
            FileNotFoundError: If the specified file does not exist.
        """
        file_path = verify_and_resolve_path(file_path)
        with open(file_path) as f:
            data = json.load(f)

        # Extract metrics from nested structure (always under "metrics" key in evaluation result files)
        metrics_data = data.get("metrics", data)

        # Filter out internal fields that shouldn't be passed to __init__
        # (e.g., _harm_definition_obj is a cached field with init=False)
        filtered_data = {k: v for k, v in metrics_data.items() if not k.startswith("_")}

        # to_json() writes numpy arrays as nested lists. Decoding back into an ndarray keeps
        # the declared field type; a value that is not a list is left alone, since only the
        # nested-list form is what to_json() produces.
        trial_scores = filtered_data.get("trial_scores")
        if isinstance(trial_scores, list):
            filtered_data["trial_scores"] = np.array(trial_scores)

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
            indicates that the model scores are typically higher than the human scores. When the model perfectly
            agrees with the gold labels (zero difference everywhere), this is reported as 0.0. When all differences
            are equal and non-zero (a systematic constant bias with no variance), the t-test is undefined and this
            is reported as NaN; consult `mean_absolute_error` for the bias magnitude in that case. NaN is the
            in-memory signal only: it is not JSON-representable, so a round trip through `to_json`,
            `from_json_file`, or the metrics registry gives ``None`` for this field instead.
        p_value (float): The p-value for the one-sample t-test above. It represents the probability of obtaining a
            difference in means as extreme as the observed difference, assuming the null hypothesis is true.
            Reported as 1.0 on perfect agreement and NaN on the constant-non-zero-bias case (see `t_statistic`,
            whose round-trip note applies here too).
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
        contested_threshold (float, Optional): The harm score at or above which a human rating counts as
            harmful when deciding whether the raters agreed. Only set when there are multiple human raters.
        num_unanimous_responses (int, Optional): Number of responses on which every human rater fell on the
            same side of ``contested_threshold``. Only set when there are multiple human raters.
        num_contested_responses (int, Optional): Number of responses on which the human raters split across
            ``contested_threshold``, so the gold label rests on a split vote rather than a consensus.
            Only set when there are multiple human raters.
        mean_absolute_error_unanimous (float, Optional): ``mean_absolute_error`` computed over the unanimous
            responses only. None when there are no unanimous responses or a single human rater.
        mean_absolute_error_contested (float, Optional): ``mean_absolute_error`` computed over the contested
            responses only. None when there are no contested responses or a single human rater. A scorer that
            is strong here and on the unanimous rows is genuinely reading the responses; one that is strong
            overall but near chance here is being carried by the easy cases, which the aggregate hides.
        baseline_mean_absolute_error (float, Optional): The mean absolute error of a scorer that ignores the
            response and always returns the median gold score of the dataset, which is the constant with the
            lowest possible mean absolute error on these labels. A scorer whose `mean_absolute_error` is not
            below this value has not beaten a constant guess on this dataset. It is computed in-sample, so it
            is the best any constant could do here and is a demanding reference. None for results recorded
            before this field existed.
    """

    mean_absolute_error: float
    mae_standard_error: float
    t_statistic: float | None
    p_value: float | None
    krippendorff_alpha_combined: float
    harm_category: str | None = field(default=None, kw_only=True)
    harm_definition: str | None = field(default=None, kw_only=True)
    harm_definition_version: str | None = field(default=None, kw_only=True)
    krippendorff_alpha_humans: float | None = None
    krippendorff_alpha_model: float | None = None
    contested_threshold: float | None = field(default=None, kw_only=True)
    num_unanimous_responses: int | None = field(default=None, kw_only=True)
    num_contested_responses: int | None = field(default=None, kw_only=True)
    mean_absolute_error_unanimous: float | None = field(default=None, kw_only=True)
    mean_absolute_error_contested: float | None = field(default=None, kw_only=True)
    baseline_mean_absolute_error: float | None = field(default=None, kw_only=True)
    _harm_definition_obj: HarmDefinition | None = field(default=None, init=False, repr=False)

    def get_harm_definition(self) -> HarmDefinition | None:
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
        trial_scores (np.ndarray | None): The raw scores from each trial. Shape is (num_trials, num_responses).
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
        scorer_identifier (ComponentIdentifier): The scorer's configuration identifier.
        metrics (M): The evaluation metrics (ObjectiveScorerMetrics or HarmScorerMetrics).
    """

    scorer_identifier: ComponentIdentifier
    metrics: M

    def __repr__(self) -> str:
        """Return a string representation of the ScorerMetricsWithIdentity."""
        metrics_type = type(self.metrics).__name__
        scorer_type = self.scorer_identifier.class_name
        return f"ScorerMetricsWithIdentity(scorer={scorer_type}, metrics_type={metrics_type})"
