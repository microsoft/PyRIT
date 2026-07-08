# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import abstractmethod
from typing import Any

from pyrit.models import ComponentIdentifier
from pyrit.output.base import PrinterBase


class ScorerPrinterBase(PrinterBase):
    """
    Abstract base class for printing scorer information.

    Subclasses must implement _get_objective_metrics and _get_harm_metrics
    for data fetching, and write_async for rendering + writing.
    """

    @abstractmethod
    def _get_objective_metrics(self, *, scorer_identifier: ComponentIdentifier) -> Any:
        """
        Fetch objective scorer evaluation metrics.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.

        Returns:
            The metrics object, or None if not found.
        """

    @abstractmethod
    def _get_harm_metrics(self, *, scorer_identifier: ComponentIdentifier, harm_category: str) -> Any:
        """
        Fetch harm scorer evaluation metrics.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            harm_category (str): The harm category to look up.

        Returns:
            The metrics object, or None if not found.
        """

    @abstractmethod
    async def render_async(self, *, scorer_identifier: ComponentIdentifier, harm_category: str | None = None) -> str:
        """
        Render scorer information and return it as a string.

        Auto-detects scorer type: if harm_category is provided, renders harm
        metrics; otherwise renders objective metrics.

        Args:
            scorer_identifier (ComponentIdentifier): The scorer identifier.
            harm_category (str | None): The harm category. None for objective scorers.

        Returns:
            str: The rendered scorer information text.
        """
