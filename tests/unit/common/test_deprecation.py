# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import warnings

from pyrit.common.deprecation import print_deprecation_message


def _old_func():
    pass


def _new_func():
    pass


class _OldClass:
    pass


class _NewClass:
    pass


def test_deprecation_warning_with_callables():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        print_deprecation_message(old_item=_old_func, new_item=_new_func, removed_in="2.0")
    assert len(w) == 1
    assert issubclass(w[0].category, DeprecationWarning)
    assert "_old_func" in str(w[0].message)
    assert "_new_func" in str(w[0].message)
    assert "2.0" in str(w[0].message)


def test_deprecation_warning_with_classes():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        print_deprecation_message(old_item=_OldClass, new_item=_NewClass, removed_in="3.0")
    assert len(w) == 1
    assert "_OldClass" in str(w[0].message)
    assert "_NewClass" in str(w[0].message)
    assert "3.0" in str(w[0].message)


def test_deprecation_warning_with_strings():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        print_deprecation_message(old_item="OldName", new_item="NewName", removed_in="4.0")
    assert len(w) == 1
    assert "OldName" in str(w[0].message)
    assert "NewName" in str(w[0].message)


def test_deprecation_warning_mixed_types():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        print_deprecation_message(old_item=_OldClass, new_item="some.new.path", removed_in="5.0")
    assert len(w) == 1
    assert "_OldClass" in str(w[0].message)
    assert "some.new.path" in str(w[0].message)
