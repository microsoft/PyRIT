# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.identifiers import ComponentIdentifier
from pyrit.printer.scorer.base import ScorerPrinterBase as ScorerPrinter


def test_scorer_printer_cannot_be_instantiated():
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        ScorerPrinter()  # type: ignore[abstract]


def test_scorer_printer_subclass_must_implement_print_objective_scorer():
    class IncompletePrinter(ScorerPrinter):
        def print_harm_scorer(self, scorer_identifier: ComponentIdentifier, *, harm_category: str) -> None:
            pass

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        IncompletePrinter()  # type: ignore[abstract]


def test_scorer_printer_subclass_must_implement_print_harm_scorer():
    class IncompletePrinter(ScorerPrinter):
        def print_objective_scorer(self, *, scorer_identifier: ComponentIdentifier) -> None:
            pass

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        IncompletePrinter()  # type: ignore[abstract]


def test_scorer_printer_complete_subclass_can_be_instantiated():
    class CompletePrinter(ScorerPrinter):
        def print_objective_scorer(self, *, scorer_identifier: ComponentIdentifier) -> None:
            pass

        def print_harm_scorer(self, scorer_identifier: ComponentIdentifier, *, harm_category: str) -> None:
            pass

        def get_objective_metrics(self, *, eval_hash: str):
            return None

        def get_harm_metrics(self, *, eval_hash: str, harm_category: str):
            return None

    printer = CompletePrinter()
    assert isinstance(printer, ScorerPrinter)
