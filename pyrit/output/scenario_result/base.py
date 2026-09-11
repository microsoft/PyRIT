# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from abc import abstractmethod
from typing import Literal

from pyrit.models import ScenarioResult
from pyrit.output.base import PrinterBase

# Which projection of a scenario result to render: the aggregate overview or the
# compact per-attack table.
ScenarioView = Literal["overview", "attacks"]


class ScenarioResultPrinterBase(PrinterBase):
    """
    Abstract base class for printing scenario results.

    Contains formatting logic. Subclasses may need to provide scorer
    printer implementations via get_scorer_printer().
    """

    @abstractmethod
    async def render_async(
        self,
        result: ScenarioResult,
        *,
        view: ScenarioView = "overview",
        attack_result_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> str:
        """
        Render a scenario result and return it as a string.

        Args:
            result (ScenarioResult): The scenario result to render.
            view (ScenarioView): Which projection to render — the aggregate ``"overview"``
                or the per-attack ``"attacks"`` table. Defaults to ``"overview"``.
            attack_result_ids (list[str] | None): For the ``"attacks"`` view, restrict to
                these attack ids. Ignored by other views. Defaults to None.
            limit (int | None): For the ``"attacks"`` view, the maximum number of attacks
                to show. Ignored by other views. Defaults to None.

        Returns:
            str: The rendered scenario result text.
        """
