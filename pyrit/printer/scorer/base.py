# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC, abstractmethod
from typing import Any

from pyrit.identifiers import ComponentIdentifier


class ScorerPrinterBase(ABC):
    """
    Abstract base class for printing scorer information.

    Subclasses implement get_objective_metrics and get_harm_metrics
    for data fetching. Framework uses the scorer registry; thin clients
    can use REST calls.
    """

    @abstractmethod
    def get_objective_metrics(self, *, eval_hash: str) -> Any:
        """
        Fetch objective scorer evaluation metrics by eval hash.

        Args:
            eval_hash (str): The evaluation hash to look up.

        Returns:
            ObjectiveScorerMetrics or None: The metrics, or None if not found.
        """

    @abstractmethod
    def get_harm_metrics(self, *, eval_hash: str, harm_category: str) -> Any:
        """
        Fetch harm scorer evaluation metrics by eval hash and category.

        Args:
            eval_hash (str): The evaluation hash to look up.
            harm_category (str): The harm category for metrics lookup.

        Returns:
            HarmScorerMetrics or None: The metrics, or None if not found.
        """

    @abstractmethod
    def print_objective_scorer(self, *, scorer_identifier: ComponentIdentifier) -> None:
        """
        Print objective scorer information including type, nested scorers, and evaluation metrics.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier to print information for.
        """

    @abstractmethod
    def print_harm_scorer(self, scorer_identifier: ComponentIdentifier, *, harm_category: str) -> None:
        """
        Print harm scorer information including type, nested scorers, and evaluation metrics.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier to print information for.
            harm_category (str): The harm category for looking up metrics.
        """
