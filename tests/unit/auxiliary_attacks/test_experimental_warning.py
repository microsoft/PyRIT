# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import importlib
import warnings

import pyrit.auxiliary_attacks
from pyrit.exceptions import ExperimentalWarning


def test_importing_auxiliary_attacks_emits_experimental_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.reload(pyrit.auxiliary_attacks)

    experimental = [w for w in caught if issubclass(w.category, ExperimentalWarning)]
    assert len(experimental) == 1
    message = str(experimental[0].message)
    assert "pyrit.auxiliary_attacks is experimental" in message
    assert "ExperimentalWarning" in message


def test_experimental_warning_is_a_future_warning_subclass() -> None:
    assert issubclass(ExperimentalWarning, FutureWarning)


def test_experimental_warning_can_be_silenced() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.filterwarnings("ignore", category=ExperimentalWarning)
        importlib.reload(pyrit.auxiliary_attacks)

    experimental = [w for w in caught if issubclass(w.category, ExperimentalWarning)]
    assert experimental == []
