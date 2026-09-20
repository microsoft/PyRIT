# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from io import StringIO

import pytest

from pyrit.common.text_helper import is_non_empty_string, read_txt


def test_read_txt_ignores_blank_lines():
    file = StringIO("first prompt\n\n   \nsecond prompt\n")

    assert read_txt(file) == [{"prompt": "first prompt"}, {"prompt": "second prompt"}]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("valid", True), ("  valid  ", True), ("", False), (" \n\t", False), (None, False), (42, False), ([], False)],
)
def test_is_non_empty_string_preserves_runtime_validation(value, expected):
    assert is_non_empty_string(value) is expected
