# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import csv
import io
from io import StringIO

import pytest

from pyrit.common.csv_helper import read_csv, strip_leading_comment_block, write_csv


@pytest.mark.parametrize("line_ending", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("leading_block", ["", "\n", " \t\n", "# metadata\n", "\n  # first\n\t\n# second\n"])
def test_strip_leading_comment_block_preserves_body(*, line_ending: str, leading_block: str) -> None:
    body = f'objective,assistant_response{line_ending}# objective,"line one\r\n### heading\rline three"{line_ending}'
    text = leading_block.replace("\n", line_ending) + body
    assert strip_leading_comment_block(text) == body


@pytest.mark.parametrize("separator", ["\v", "\f", "\x85", "\u2028", "\u2029"])
def test_strip_leading_comment_block_preserves_non_newline_separators(separator: str) -> None:
    body = f"value{separator}# header\none\n"
    assert strip_leading_comment_block("# metadata\n" + body) == body


@pytest.mark.parametrize("text", ["", "\n\t\n", "# comment", "\r\n# comment\r\n \r"])
def test_strip_leading_comment_block_without_content(text: str) -> None:
    assert strip_leading_comment_block(text) == ""


def test_read_csv_returns_list_of_dicts():
    data = "name,age\nAlice,30\nBob,25\n"
    result = read_csv(StringIO(data))
    assert result == [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]


def test_read_csv_empty_body():
    data = "name,age\n"
    result = read_csv(StringIO(data))
    assert result == []


def test_read_csv_single_column():
    data = "value\nfoo\nbar\n"
    result = read_csv(StringIO(data))
    assert result == [{"value": "foo"}, {"value": "bar"}]


def test_write_csv_produces_expected_output():
    output = StringIO()
    examples = [{"col1": "a", "col2": "b"}, {"col1": "c", "col2": "d"}]
    write_csv(output, examples)
    output.seek(0)
    lines = output.read().strip().splitlines()
    assert lines[0] == "col1,col2"
    assert lines[1] == "a,b"
    assert lines[2] == "c,d"


def test_write_then_read_roundtrip():
    examples = [{"x": "1", "y": "2"}, {"x": "3", "y": "4"}]
    buf = StringIO()
    write_csv(buf, examples)
    buf.seek(0)
    result = read_csv(buf)
    assert result == examples


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


def test_write_csv_covers_keys_only_later_rows_have():
    output = StringIO()
    examples = [
        {"name": "Alice"},
        {"name": "Bob", "note": "extra"},
    ]
    write_csv(output, examples)
    output.seek(0)
    result = read_csv(output)
    assert result == [{"name": "Alice", "note": ""}, {"name": "Bob", "note": "extra"}]


def test_write_csv_keeps_first_seen_column_order():
    output = StringIO()
    examples = [
        {"a": "1", "b": "2"},
        {"c": "3", "a": "4"},
    ]
    write_csv(output, examples)
    output.seek(0)
    lines = output.read().strip().splitlines()
    assert lines[0] == "a,b,c"


def test_strip_leading_comment_block_preserves_crlf_and_cr_inside_quoted_cells():
    text = '# dataset_version=1.0\r\nobjective,assistant_response\r\nplain,"line one\r\n### heading\rline three"\r\n'
    body = strip_leading_comment_block(text)
    assert body == 'objective,assistant_response\r\nplain,"line one\r\n### heading\rline three"\r\n'


def test_strip_leading_comment_block_handles_cr_only_record_separators():
    text = "# dataset_version=1.0\robjective,assistant_response\rplain,refusal response\r"
    body = strip_leading_comment_block(text)
    assert body == "objective,assistant_response\rplain,refusal response\r"
    assert list(csv.DictReader(StringIO(body, newline=""))) == [
        {"objective": "plain", "assistant_response": "refusal response"}
    ]
