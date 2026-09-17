# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from io import StringIO

import pytest

from pyrit.common.text_helper import escape_control_characters, read_txt


def test_read_txt_ignores_blank_lines():
    file = StringIO("first prompt\n\n   \nsecond prompt\n")

    assert read_txt(file) == [{"prompt": "first prompt"}, {"prompt": "second prompt"}]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("\x1b[2J", "\\x1b[2J"),
        ("\x1b]52;c;ZWNobw==\x07", "\\x1b]52;c;ZWNobw==\\x07"),
        ("\x9b31m\x9d0;title\x9c", "\\x9b31m\\x9d0;title\\x9c"),
        ("visible\roverwritten", "visible\\roverwritten"),
        ("a\x00b\x7fc\x08d", "a\\x00b\\x7fc\\x08d"),
    ],
)
def test_escape_control_characters_makes_control_characters_visible(text: str, expected: str):
    assert escape_control_characters(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "plain text",
        "line one\nline two",
        "column\tseparated",
        "non-ascii é 中文 ␛",
        "already escaped \\x1b[2J",
    ],
)
def test_escape_control_characters_leaves_other_text_unchanged(text: str):
    assert escape_control_characters(text) == text
