# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.output.attack_result.base import AttackResultPrinterBase


@pytest.mark.parametrize(
    "milliseconds,expected",
    [
        (0, "0ms"),
        (500, "500ms"),
        (999, "999ms"),
        (1000, "1.00s"),
        (2500, "2.50s"),
        (59999, "60.00s"),
        (60000, "1m 0s"),
        (90000, "1m 30s"),
        (150000, "2m 30s"),
    ],
)
def test_format_time(milliseconds, expected):
    assert AttackResultPrinterBase._format_time(milliseconds) == expected
