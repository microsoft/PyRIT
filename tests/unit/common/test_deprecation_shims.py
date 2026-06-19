# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the Phase 18 ``pyrit.common`` reverse-guard relocation shims.

``pyrit.common.data_url_converter`` moved to ``pyrit.memory.storage`` and
``pyrit.common.question_answer_helpers`` moved to ``pyrit.score``. The old module
paths still forward to the new locations but emit a ``DeprecationWarning`` per
name. These tests pin that contract. The shims will be removed in 0.16.0.
"""

from __future__ import annotations

import importlib
import warnings

import pytest

import pyrit.common.data_url_converter as data_url_shim
import pyrit.common.question_answer_helpers as question_answer_shim
import pyrit.memory.storage.data_url_converter as new_data_url
import pyrit.score.question_answer_helpers as new_question_answer

MODULE_SHIM_PAIRS = [
    (
        data_url_shim,
        new_data_url,
        "pyrit.common.data_url_converter",
        "pyrit.memory.storage.data_url_converter",
    ),
    (
        question_answer_shim,
        new_question_answer,
        "pyrit.common.question_answer_helpers",
        "pyrit.score.question_answer_helpers",
    ),
]


@pytest.mark.parametrize("shim_mod, new_mod, old_path, new_path", MODULE_SHIM_PAIRS)
def test_module_shim_forwards_every_name(shim_mod, new_mod, old_path, new_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for name in shim_mod.__all__:
            assert getattr(shim_mod, name) is getattr(new_mod, name), f"{old_path}.{name} did not forward"


@pytest.mark.parametrize("shim_mod, new_mod, old_path, new_path", MODULE_SHIM_PAIRS)
def test_module_shim_warns_once_per_name(shim_mod, new_mod, old_path, new_path):
    # Reload the shim to reset its internal warn-once closure for a clean count.
    shim_mod = importlib.reload(shim_mod)
    for name in shim_mod.__all__:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            getattr(shim_mod, name)
            getattr(shim_mod, name)

        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep) == 1, f"Expected 1 DeprecationWarning for {old_path}.{name}, got {len(dep)}"
        message = str(dep[0].message)
        assert f"{old_path}.{name}" in message
        assert f"{new_path}.{name}" in message
        assert "0.16.0" in message


@pytest.mark.parametrize("shim_mod, new_mod, old_path, new_path", MODULE_SHIM_PAIRS)
def test_module_shim_attribute_error_for_unknown_name(shim_mod, new_mod, old_path, new_path):
    with pytest.raises(AttributeError, match=f"module {old_path!r} has no attribute"):
        _ = shim_mod.definitely_not_a_real_name


@pytest.mark.parametrize("shim_mod, new_mod, old_path, new_path", MODULE_SHIM_PAIRS)
def test_module_shim_dir_returns_sorted_all(shim_mod, new_mod, old_path, new_path):
    assert dir(shim_mod) == sorted(shim_mod.__all__)
