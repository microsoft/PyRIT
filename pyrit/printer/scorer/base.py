# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import abstractmethod
from typing import Any

from pyrit.identifiers import ComponentIdentifier
from pyrit.printer.base import PrinterBase


class ScorerPrinterBase(PrinterBase):
    """
    Abstract base class for printing scorer information.

    Subclasses must implement _get_objective_metrics and _get_harm_metrics
    for data fetching, and write_async for rendering + writing.
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
    async def write_async(
        self, *, scorer_identifier: ComponentIdentifier, harm_category: str | None = None
    ) -> None:
        """
        Render and write scorer information to the configured sink.

        Auto-detects scorer type: if harm_category is provided, renders harm
        metrics; otherwise renders objective metrics.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            harm_category (str | None): The harm category. None for objective scorers.
        """

    async def print_objective_scorer(self, *, scorer_identifier: ComponentIdentifier) -> None:
        """
        Deprecated. Use write_async instead.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
        """
        await self.write_async(scorer_identifier=scorer_identifier)

    async def print_harm_scorer(self, *, scorer_identifier: ComponentIdentifier, harm_category: str) -> None:
        """
        Deprecated. Use write_async instead.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            harm_category (str): The harm category.
        """
        await self.write_async(scorer_identifier=scorer_identifier, harm_category=harm_category)
