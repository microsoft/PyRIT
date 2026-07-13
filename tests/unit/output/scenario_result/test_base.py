# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.output.scenario_result.base import ScenarioResultPrinterBase


def test_scenario_result_printer_cannot_be_instantiated():
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        ScenarioResultPrinterBase()  # type: ignore[abstract]
