# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import ABC, abstractmethod

from pyrit.identifiers import ScorerIdentifier


class ScorerPrinter(ABC):
    """
    Abstract base class for printing scorer information.

    This interface defines the contract for printing scorer details including
    type information, nested sub-scorers, and evaluation metrics from the registry.
    Implementations can render output to console, logs, files, or other outputs.
    """

    @abstractmethod
    def print_objective_scorer(self, *, scorer_identifier: ScorerIdentifier) -> None:
        """
        Print objective scorer information including type, nested scorers, and evaluation metrics.

        This method displays:
        - Scorer type and identity information
        - Nested sub-scorers (for composite scorers)
        - Objective evaluation metrics (accuracy, precision, recall, F1) from the registry

        Args:
            scorer_identifier (ScorerIdentifier): The scorer identifier to print information for.
        """
        pass

    @abstractmethod
    def print_harm_scorer(self, scorer_identifier: ScorerIdentifier, *, harm_category: str) -> None:
        """
        Print harm scorer information including type, nested scorers, and evaluation metrics.

        This method displays:
        - Scorer type and identity information
        - Nested sub-scorers (for composite scorers)
        - Harm evaluation metrics (MAE, Krippendorff alpha) from the registry

        Args:
            scorer_identifier (ScorerIdentifier): The scorer identifier to print information for.
            harm_category (str): The harm category for looking up metrics (e.g., "hate_speech", "violence").
        """
        pass
