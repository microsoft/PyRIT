# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

import pytest

from pyrit.converter import AnsiAttackConverter
from pyrit.executor.promptgen.fuzzer import FuzzerResult, FuzzerResultPrinter

# The printer's own colors are SGR sequences, so those are stripped before asserting - but only
# when the printer was asked for them. With colors disabled nothing may reach the terminal.
_OWN_COLORS = re.compile(r"\x1b\[[0-9;]*m")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _assert_no_live_control_characters(printed: str, *, enable_colors: bool) -> None:
    stripped = _OWN_COLORS.sub("", printed) if enable_colors else printed
    assert not _CONTROL_CHARACTERS.search(stripped)
    assert "\\x" in printed


@pytest.mark.usefixtures("patch_central_database")
class TestFuzzerResultPrinter:
    @pytest.mark.parametrize("enable_colors", [True, False])
    @pytest.mark.parametrize("payload", AnsiAttackConverter.LIVE_PAYLOADS)
    def test_print_result_escapes_control_characters(self, payload: str, enable_colors: bool, capsys) -> None:
        result = FuzzerResult(successful_templates=[f"template {payload} {{{{ prompt }}}}"])

        FuzzerResultPrinter(enable_colors=enable_colors).print_result(result)

        _assert_no_live_control_characters(capsys.readouterr().out, enable_colors=enable_colors)

    @pytest.mark.parametrize("payload", AnsiAttackConverter.LIVE_PAYLOADS)
    def test_print_templates_only_escapes_control_characters(self, payload: str, capsys) -> None:
        result = FuzzerResult(successful_templates=[f"template {payload} {{{{ prompt }}}}"])

        FuzzerResultPrinter().print_templates_only(result)

        _assert_no_live_control_characters(capsys.readouterr().out, enable_colors=False)

    def test_print_result_escapes_carriage_returns(self, capsys) -> None:
        # print_result wraps before it colors, and textwrap replaces a lone carriage return with a
        # space, so the template has to be escaped before it is wrapped.
        result = FuzzerResult(successful_templates=["alpha\rbeta {{ prompt }}"])

        FuzzerResultPrinter(enable_colors=False).print_result(result)

        assert "alpha\\rbeta" in capsys.readouterr().out
