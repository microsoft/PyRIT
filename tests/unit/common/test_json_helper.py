# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from io import StringIO

from pyrit.common.json_helper import read_jsonl


def test_read_jsonl_ignores_blank_lines():
    file = StringIO('{"prompt": "first"}\n\n{"prompt": "second"}\n')

    result = read_jsonl(file)

    assert result == [{"prompt": "first"}, {"prompt": "second"}]
