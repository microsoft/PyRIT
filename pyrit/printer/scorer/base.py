# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC, abstractmethod

from pyrit.identifiers import ComponentIdentifier


class ScorerPrinterBase(ABC):
    """
    Abstract base class for printing scorer information.

    Subclasses must implement print_objective_scorer and print_harm_scorer.
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
