# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import abstractmethod
from typing import Any

from pyrit.identifiers import ComponentIdentifier
from pyrit.printer.base import PrinterBase


class ScorerPrinterBase(PrinterBase):
    """
    Abstract base class for printing scorer information.

    Subclasses must implement get_objective_metrics and get_harm_metrics
    for data fetching. Orchestration methods (print_objective_scorer,
    print_harm_scorer) live in concrete formatting subclasses.
    """

    @abstractmethod
    def _get_objective_metrics(self, *, eval_hash: str) -> Any:
        """
        Fetch objective scorer evaluation metrics.

        Args:
            eval_hash (str): The evaluation hash to look up.

        Returns:
            The metrics object, or None if not found.
        """

    @abstractmethod
    def _get_harm_metrics(self, *, eval_hash: str, harm_category: str) -> Any:
        """
        Fetch harm scorer evaluation metrics.

        Args:
            eval_hash (str): The evaluation hash to look up.
            harm_category (str): The harm category to look up.

        Returns:
            The metrics object, or None if not found.
        """

    @abstractmethod
    def print_objective_scorer(self, *, scorer_identifier: ComponentIdentifier) -> None:
        """
        Print objective scorer information including type, nested scorers, and evaluation metrics.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier to print information for.
        """

    @abstractmethod
    def print_harm_scorer(self, *, scorer_identifier: ComponentIdentifier, harm_category: str) -> None:
        """
        Print harm scorer information including type, nested scorers, and evaluation metrics.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier to print information for.
            harm_category (str): The harm category for looking up metrics.
        """
