# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The parameter contract moved to ``pyrit.models.parameter``.

These tests pin the deprecation shims: importing ``Parameter`` (or the coercion
helpers) from ``pyrit.common`` / ``pyrit.common.parameter`` must still resolve to
the canonical object but emit a ``DeprecationWarning``.
"""

import importlib

import pytest

import pyrit.common
import pyrit.common.parameter as common_parameter
from pyrit.models.parameter import Parameter as CanonicalParameter


def test_parameter_from_common_parameter_warns_and_resolves():
    # Reload to reset the shim's one-time "already warned" state so the warning
    # fires deterministically regardless of earlier imports in the session.
    importlib.reload(common_parameter)

    with pytest.warns(DeprecationWarning, match=r"pyrit\.models\.parameter\.Parameter"):
        resolved = common_parameter.Parameter

    assert resolved is CanonicalParameter


def test_parameter_from_common_package_warns_and_resolves():
    importlib.reload(pyrit.common)

    with pytest.warns(DeprecationWarning, match=r"pyrit\.models\.Parameter"):
        resolved = pyrit.common.Parameter

    assert resolved is CanonicalParameter


def test_common_parameter_unknown_name_raises_attribute_error():
    importlib.reload(common_parameter)

    missing_attr = "does_not_exist"
    with pytest.raises(AttributeError):
        getattr(common_parameter, missing_attr)


def test_parameter_no_longer_in_common_all():
    assert "Parameter" not in pyrit.common.__all__
