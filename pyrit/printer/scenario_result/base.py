# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import abstractmethod

from pyrit.models.scenario_result import ScenarioResult
from pyrit.printer.base import PrinterBase


class ScenarioResultPrinterBase(PrinterBase):
    """
    Abstract base class for printing scenario results.

    Contains formatting logic. Subclasses may need to provide scorer
    printer implementations via get_scorer_printer().
    """

    @abstractmethod
    async def write_async(self, result: ScenarioResult) -> None:
        """
        Render and write a scenario result summary to the configured sink.

        Args:
            result (ScenarioResult): The scenario result to summarize.
        """

    async def print_summary_async(self, result: ScenarioResult) -> None:
        """
        Deprecated. Use write_async instead.

        Args:
            result (ScenarioResult): The scenario result to summarize.
        """
        await self.write_async(result)
