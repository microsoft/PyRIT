# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.identifiers import ComponentIdentifier
from pyrit.printer.scorer.base import ScorerPrinterBase as ScorerPrinter


def test_scorer_printer_cannot_be_instantiated():
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        ScorerPrinter()  # type: ignore[abstract]


def test_scorer_printer_subclass_must_implement_get_objective_metrics():
    class IncompletePrinter(ScorerPrinter):
        def _get_harm_metrics(self, *, eval_hash: str, harm_category: str):
            return None

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        IncompletePrinter()  # type: ignore[abstract]


def test_scorer_printer_subclass_must_implement_get_harm_metrics():
    class IncompletePrinter(ScorerPrinter):
        def _get_objective_metrics(self, *, eval_hash: str):
            return None

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        IncompletePrinter()  # type: ignore[abstract]


def test_scorer_printer_subclass_must_implement_write_async():
    class IncompletePrinter(ScorerPrinter):
        def _get_objective_metrics(self, *, eval_hash: str):
            return None

        def _get_harm_metrics(self, *, eval_hash: str, harm_category: str):
            return None

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        IncompletePrinter()  # type: ignore[abstract]


def test_scorer_printer_complete_subclass_can_be_instantiated():
    class CompletePrinter(ScorerPrinter):
        def _get_objective_metrics(self, *, eval_hash: str):
            return None

        def _get_harm_metrics(self, *, eval_hash: str, harm_category: str):
            return None

        async def render_async(
            self, *, scorer_identifier: ComponentIdentifier, harm_category: str | None = None
        ) -> str:
            return ""

    printer = CompletePrinter()
    assert isinstance(printer, ScorerPrinter)
