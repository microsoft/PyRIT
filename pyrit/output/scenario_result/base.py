# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import abstractmethod

from pyrit.models import ScenarioResult
from pyrit.output.base import PrinterBase


class ScenarioResultPrinterBase(PrinterBase):
    """
    Abstract base class for printing scenario results.

    Contains formatting logic. Subclasses may need to provide scorer
    printer implementations via get_scorer_printer().
    """

    @abstractmethod
    async def render_async(self, result: ScenarioResult) -> str:
        """
        Render a scenario result summary and return it as a string.

        Args:
            result (ScenarioResult): The scenario result to summarize.

        Returns:
            str: The rendered scenario result text.
        """
