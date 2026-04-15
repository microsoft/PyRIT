# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.executor.attack.printer.attack_result_printer import AttackResultPrinter
from pyrit.models import AttackOutcome


class _ConcreteAttackResultPrinter(AttackResultPrinter):
    async def print_result_async(self, result, **kwargs):
        pass

    async def print_conversation_async(self, result, **kwargs):
        pass

    async def print_summary_async(self, result):
        pass


@pytest.fixture
def printer():
    return _ConcreteAttackResultPrinter()


def test_get_outcome_icon_success():
    assert AttackResultPrinter._get_outcome_icon(AttackOutcome.SUCCESS) == "\u2705"


def test_get_outcome_icon_failure():
    assert AttackResultPrinter._get_outcome_icon(AttackOutcome.FAILURE) == "\u274c"


def test_get_outcome_icon_undetermined():
    assert AttackResultPrinter._get_outcome_icon(AttackOutcome.UNDETERMINED) == "\u2753"


def test_format_time_milliseconds():
    assert AttackResultPrinter._format_time(500) == "500ms"


def test_format_time_zero():
    assert AttackResultPrinter._format_time(0) == "0ms"


def test_format_time_boundary_999():
    assert AttackResultPrinter._format_time(999) == "999ms"


def test_format_time_seconds():
    assert AttackResultPrinter._format_time(1000) == "1.00s"


def test_format_time_seconds_decimal():
    assert AttackResultPrinter._format_time(2500) == "2.50s"


def test_format_time_boundary_59999():
    assert AttackResultPrinter._format_time(59999) == "60.00s"


def test_format_time_minutes():
    assert AttackResultPrinter._format_time(60000) == "1m 0s"


def test_format_time_minutes_and_seconds():
    assert AttackResultPrinter._format_time(90000) == "1m 30s"


def test_format_time_multiple_minutes():
    assert AttackResultPrinter._format_time(150000) == "2m 30s"
