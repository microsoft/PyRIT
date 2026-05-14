# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any

from pyrit.identifiers import ComponentIdentifier
from pyrit.printer.scorer.console import ConsoleScorerPrinterBase


class ConsoleScorerPrinter(ConsoleScorerPrinterBase):
    """
    Framework console printer for scorer information.

    Thin subclass that implements metrics fetching via the scorer evaluation registry.
    All formatting logic lives in ConsoleScorerPrinterBase.
    """

    def get_objective_metrics(self, *, eval_hash: str) -> Any:
        """
        Fetch objective scorer evaluation metrics from the registry.

        Args:
            eval_hash (str): The evaluation hash to look up.

        Returns:
            ObjectiveScorerMetrics or None: The metrics, or None if not found.
        """
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_objective_metrics_by_eval_hash,
        )

        return find_objective_metrics_by_eval_hash(eval_hash=eval_hash)

    def get_harm_metrics(self, *, eval_hash: str, harm_category: str) -> Any:
        """
        Fetch harm scorer evaluation metrics from the registry.

        Args:
            eval_hash (str): The evaluation hash to look up.
            harm_category (str): The harm category for metrics lookup.

        Returns:
            HarmScorerMetrics or None: The metrics, or None if not found.
        """
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_harm_metrics_by_eval_hash,
        )

        return find_harm_metrics_by_eval_hash(eval_hash=eval_hash, harm_category=harm_category)
