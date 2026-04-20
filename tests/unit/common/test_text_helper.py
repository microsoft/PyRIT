# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from io import StringIO

from pyrit.common.text_helper import read_txt


def test_read_txt_ignores_blank_lines():
    file = StringIO("first prompt\n\n   \nsecond prompt\n")

    assert read_txt(file) == [{"prompt": "first prompt"}, {"prompt": "second prompt"}]
