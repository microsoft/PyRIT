# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import JsonValue

from pyrit.models.score._argument_paths import read_argument, split_argument_path

ARGUMENTS: dict[str, JsonValue] = {
    "to": "a@example.com",
    "recipient": {"email": "b@example.com", "address": {"city": "Leeds"}},
    "attachments": [{"name": "report.pdf"}],
    "empty": None,
}


@pytest.mark.parametrize(
    ("path", "keys"),
    [("to", ("to",)), ("recipient.email", ("recipient", "email")), ("a.b.c", ("a", "b", "c"))],
)
def test_split_argument_path_returns_each_object_key(path: str, keys: tuple[str, ...]) -> None:
    assert split_argument_path(path) == keys


@pytest.mark.parametrize("path", ["", ".", "a.", ".a", "a..b"])
def test_split_argument_path_rejects_an_empty_key(path: str) -> None:
    with pytest.raises(ValueError, match="single dots"):
        split_argument_path(path)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("to", (True, "a@example.com")),
        ("recipient.email", (True, "b@example.com")),
        ("recipient.address.city", (True, "Leeds")),
        ("recipient.address", (True, {"city": "Leeds"})),
        ("empty", (True, None)),
        ("recipient.phone", (False, None)),
        ("to.domain", (False, None)),
        ("attachments.0.name", (False, None)),
        ("attachments.name", (False, None)),
        ("empty.value", (False, None)),
    ],
)
def test_read_argument_follows_object_keys_only(path: str, expected: tuple[bool, JsonValue]) -> None:
    assert read_argument(ARGUMENTS, path=path) == expected


def test_read_argument_reads_a_name_that_is_not_a_path_as_one_literal_key() -> None:
    assert read_argument({"a..b": "x"}, path="a..b") == (True, "x")
    assert read_argument({"a": {"": {"b": "x"}}}, path="a..b") == (False, None)


def test_read_argument_reads_a_top_level_key_that_is_the_whole_name_first() -> None:
    # A name written when names were plain keys keeps reading the key it always read.
    assert read_argument({"recipient.email": "x"}, path="recipient.email") == (True, "x")
    assert read_argument({"recipient.email": "x", "recipient": {"email": "y"}}, path="recipient.email") == (True, "x")
    assert read_argument({"recipient": {"email": "y"}}, path="recipient.email") == (True, "y")
