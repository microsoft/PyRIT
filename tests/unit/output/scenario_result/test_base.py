# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.models import ScenarioResult
from pyrit.output.scenario_result.base import ScenarioResultPrinterBase


def test_scenario_result_printer_cannot_be_instantiated():
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        ScenarioResultPrinterBase()  # type: ignore[abstract]


async def test_print_summary_async_emits_deprecation_warning_and_delegates():
    """``print_summary_async`` is a deprecated shim that should warn and call ``write_async``."""

    class _MinimalPrinter(ScenarioResultPrinterBase):
        def __init__(self) -> None:
            super().__init__()
            self.write_async = AsyncMock()

        async def render_async(self, result: ScenarioResult) -> str:
            return ""

    printer = _MinimalPrinter()
    result = MagicMock(spec=ScenarioResult)

    with pytest.warns(DeprecationWarning, match="print_summary_async"):
        await printer.print_summary_async(result)

    printer.write_async.assert_awaited_once_with(result)
