# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import json
import math
from typing import Any

from pyrit.models import ComponentIdentifier, ScorerIdentifier, project_behavioral_identity
from pyrit.output.scorer.base import ScorerPrinterBase
from pyrit.output.sink import Sink


class JsonScorerPrinter(ScorerPrinterBase):
    """
    JSON printer for scorer information.

    Surfaces the same attributes the pretty scorer printer shows — class name,
    behavioral params, nested children, and evaluation metrics — as structured
    data, but without the pretty printer's value truncation. ``build`` returns the
    dict so a larger document (e.g. the scenario overview) can embed it;
    ``render_async`` serializes it. This format class does no data I/O; the
    ``*MemoryPrinter`` leaf supplies metrics.
    """

    def __init__(self, *, sink: Sink | None = None, indent: int = 2) -> None:
        """
        Initialize the JSON scorer printer.

        Args:
            sink (Sink | None): Output sink. Defaults to StdoutSink().
            indent (int): JSON indentation width. Defaults to 2.
        """
        super().__init__(sink=sink)
        self._indent = indent

    def build(self, *, scorer_identifier: ComponentIdentifier, harm_category: str | None = None) -> dict[str, Any]:
        """
        Build the structured (dict) representation of a scorer.

        Scorer identity is projected to its behavioral view (operational params
        dropped) to match the pretty printer; metrics are fetched from the leaf's
        data hook using the original identifier's eval hash.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            harm_category (str | None): The harm category. None for objective scorers.

        Returns:
            dict[str, Any]: The scorer's class name, params, children, and metrics.
        """
        behavioral_identifier = project_behavioral_identity(scorer_identifier, identifier_type=ScorerIdentifier)
        info = self._build_scorer_info(behavioral_identifier)
        if harm_category is not None:
            metrics = self._get_harm_metrics(scorer_identifier=scorer_identifier, harm_category=harm_category)
        else:
            metrics = self._get_objective_metrics(scorer_identifier=scorer_identifier)
        info["metrics"] = _metrics_to_dict(metrics)
        return info

    async def render_async(self, *, scorer_identifier: ComponentIdentifier, harm_category: str | None = None) -> str:
        """
        Render scorer information as a JSON string.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            harm_category (str | None): The harm category. None for objective scorers.

        Returns:
            str: The scorer information serialized as indented JSON.
        """
        payload = self.build(scorer_identifier=scorer_identifier, harm_category=harm_category)
        return json.dumps(payload, indent=self._indent, default=str, ensure_ascii=False)

    def _build_scorer_info(self, scorer_identifier: ComponentIdentifier) -> dict[str, Any]:
        """
        Build the class name / params / children block for a scorer, recursing into children.

        Args:
            scorer_identifier (ComponentIdentifier): The (behavioral) scorer identifier.

        Returns:
            dict[str, Any]: The scorer's ``class_name``, untruncated ``params`` (when any),
                and nested ``children`` (when any).
        """
        info: dict[str, Any] = {"class_name": scorer_identifier.class_name}
        if scorer_identifier.params:
            info["params"] = dict(scorer_identifier.params)
        children: dict[str, Any] = {}
        for child_name, child_value in scorer_identifier.children.items():
            if isinstance(child_value, list):
                children[child_name] = [self._build_scorer_info(child) for child in child_value]
            else:
                children[child_name] = self._build_scorer_info(child_value)
        if children:
            info["children"] = children
        return info


class JsonScorerMemoryPrinter(JsonScorerPrinter):
    """JSON scorer printer that fetches evaluation metrics from the registry."""

    def _get_objective_metrics(self, *, scorer_identifier: ComponentIdentifier) -> Any:
        """
        Fetch objective scorer evaluation metrics from the registry.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.

        Returns:
            Any: The metrics dataclass, or None if not found.
        """
        from pyrit.models import ScorerEvaluationIdentifier
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_objective_metrics_by_eval_hash,
        )

        eval_hash = ScorerEvaluationIdentifier(scorer_identifier).eval_hash
        return find_objective_metrics_by_eval_hash(eval_hash=eval_hash)

    def _get_harm_metrics(self, *, scorer_identifier: ComponentIdentifier, harm_category: str) -> Any:
        """
        Fetch harm scorer evaluation metrics from the registry.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            harm_category (str): The harm category to look up.

        Returns:
            Any: The metrics dataclass, or None if not found.
        """
        from pyrit.models import ScorerEvaluationIdentifier
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_harm_metrics_by_eval_hash,
        )

        eval_hash = ScorerEvaluationIdentifier(scorer_identifier).eval_hash
        return find_harm_metrics_by_eval_hash(eval_hash=eval_hash, harm_category=harm_category)


def _metrics_to_dict(metrics: Any | None) -> dict[str, Any] | None:
    """
    Convert a scorer metrics dataclass to a JSON-safe dict.

    Drops ``trial_scores`` (a numpy array that is not JSON-serializable) and maps any
    non-finite float (``NaN`` / ``Infinity``, which some metrics report legitimately) to
    ``None``, since those tokens are not valid JSON and are rejected by strict parsers.

    Args:
        metrics (Any | None): The metrics dataclass, or None.

    Returns:
        dict[str, Any] | None: The serialized metrics, or None.
    """
    if metrics is None:
        return None
    data = dataclasses.asdict(metrics)
    data.pop("trial_scores", None)
    # NaN / Infinity aren't valid JSON, so map non-finite floats to null for strict parsers.
    for key, value in data.items():
        if isinstance(value, float) and not math.isfinite(value):
            data[key] = None
    return data
