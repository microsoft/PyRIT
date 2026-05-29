# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import abstractmethod

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.scenario_result import ScenarioResult
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

    async def print_summary_async(self, result: ScenarioResult) -> None:
        """
        Use ``write_async`` instead. This method is deprecated.

        Args:
            result (ScenarioResult): The scenario result to summarize.
        """
        print_deprecation_message(old_item="print_summary_async", new_item="write_async", removed_in="0.16.0")
        await self.write_async(result)
