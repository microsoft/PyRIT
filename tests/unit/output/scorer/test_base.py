# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock

import pytest

from pyrit.identifiers import ComponentIdentifier
from pyrit.output.scorer.base import ScorerPrinterBase


def test_scorer_printer_cannot_be_instantiated():
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        ScorerPrinterBase()  # type: ignore[abstract]


def test_scorer_printer_subclass_must_implement_get_objective_metrics():
    class IncompletePrinter(ScorerPrinterBase):
        def _get_harm_metrics(self, *, scorer_identifier: ComponentIdentifier, harm_category: str):
            return None

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        IncompletePrinter()  # type: ignore[abstract]


def test_scorer_printer_subclass_must_implement_get_harm_metrics():
    class IncompletePrinter(ScorerPrinterBase):
        def _get_objective_metrics(self, *, scorer_identifier: ComponentIdentifier):
            return None

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        IncompletePrinter()  # type: ignore[abstract]


def test_scorer_printer_subclass_must_implement_write_async():
    class IncompletePrinter(ScorerPrinterBase):
        def _get_objective_metrics(self, *, scorer_identifier: ComponentIdentifier):
            return None

        def _get_harm_metrics(self, *, scorer_identifier: ComponentIdentifier, harm_category: str):
            return None

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        IncompletePrinter()  # type: ignore[abstract]


def test_scorer_printer_complete_subclass_can_be_instantiated():
    class CompletePrinter(ScorerPrinterBase):
        def _get_objective_metrics(self, *, scorer_identifier: ComponentIdentifier):
            return None

        def _get_harm_metrics(self, *, scorer_identifier: ComponentIdentifier, harm_category: str):
            return None

        async def render_async(
            self, *, scorer_identifier: ComponentIdentifier, harm_category: str | None = None
        ) -> str:
            return ""

    printer = CompletePrinter()
    assert isinstance(printer, ScorerPrinterBase)


def _make_complete_printer() -> ScorerPrinterBase:
    class CompletePrinter(ScorerPrinterBase):
        def __init__(self) -> None:
            super().__init__()
            self.write_async = AsyncMock()

        def _get_objective_metrics(self, *, scorer_identifier: ComponentIdentifier):
            return None

        def _get_harm_metrics(self, *, scorer_identifier: ComponentIdentifier, harm_category: str):
            return None

        async def render_async(
            self, *, scorer_identifier: ComponentIdentifier, harm_category: str | None = None
        ) -> str:
            return ""

    return CompletePrinter()


async def test_print_objective_scorer_emits_deprecation_warning_and_delegates():
    """``print_objective_scorer`` is a deprecated shim that should warn and call ``write_async``."""
    printer = _make_complete_printer()
    scorer_identifier = ComponentIdentifier(class_name="TestScorer", class_module="tests")

    with pytest.warns(DeprecationWarning, match="print_objective_scorer"):
        await printer.print_objective_scorer(scorer_identifier=scorer_identifier)

    printer.write_async.assert_awaited_once_with(scorer_identifier=scorer_identifier)


async def test_print_harm_scorer_emits_deprecation_warning_and_delegates():
    """``print_harm_scorer`` is a deprecated shim that should warn and call ``write_async``."""
    printer = _make_complete_printer()
    scorer_identifier = ComponentIdentifier(class_name="TestScorer", class_module="tests")

    with pytest.warns(DeprecationWarning, match="print_harm_scorer"):
        await printer.print_harm_scorer(scorer_identifier=scorer_identifier, harm_category="violence")

    printer.write_async.assert_awaited_once_with(scorer_identifier=scorer_identifier, harm_category="violence")
