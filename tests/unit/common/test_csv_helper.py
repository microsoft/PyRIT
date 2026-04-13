# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io

from pyrit.common.csv_helper import write_csv


def test_write_csv_empty_examples_writes_nothing():
    file = io.StringIO()
    write_csv(file, [])
    assert file.getvalue() == ""


def test_write_csv_writes_header_and_rows():
    file = io.StringIO()
    write_csv(file, [{"name": "alice", "role": "admin"}])
    lines = file.getvalue().strip().splitlines()
    assert lines[0] == "name,role"
    assert lines[1] == "alice,admin"
