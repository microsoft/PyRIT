# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import JsonValue

from pyrit.models.score._argument_paths import (
    holds_only_kept_arguments,
    keep_argument_paths,
    validate_kept_argument_paths,
)

ARGUMENTS: dict[str, JsonValue] = {
    "to": "a@example.com",
    "recipient": {"email": "b@example.com", "name": "private-name", "address": {"city": "Leeds"}},
    "attachments": [{"name": "private-file"}],
    "body": "private-body",
}


@pytest.mark.parametrize(
    ("kept", "expected"),
    [
        (("to",), {"to": "a@example.com"}),
        (("recipient.email",), {"recipient": {"email": "b@example.com"}}),
        (("recipient.address.city", "to"), {"recipient": {"address": {"city": "Leeds"}}, "to": "a@example.com"}),
        (
            ("recipient.email", "recipient.address"),
            {"recipient": {"email": "b@example.com", "address": {"city": "Leeds"}}},
        ),
        (("recipient.phone", "cc", "to.domain", "attachments.name", "attachments.0.name"), {}),
    ],
)
def test_keep_argument_paths_copies_only_the_named_fields(
    kept: tuple[str, ...], expected: dict[str, JsonValue]
) -> None:
    kept_view = keep_argument_paths(ARGUMENTS, kept=kept)

    assert kept_view == expected
    assert "private-" not in str(kept_view)
    assert holds_only_kept_arguments(kept_view, kept=kept)


def test_keep_argument_paths_does_not_alias_the_source_objects() -> None:
    kept_view = keep_argument_paths(ARGUMENTS, kept=("recipient.email",))

    assert kept_view["recipient"] is not ARGUMENTS["recipient"]


@pytest.mark.parametrize(
    ("arguments", "kept"),
    [
        ({"recipient": {"email": "b@example.com", "name": "private-name"}}, ("recipient.email",)),
        ({"recipient": "b@example.com"}, ("recipient.email",)),
        ({"recipient.email": "b@example.com"}, ("recipient.email",)),
        ({"to": "a@example.com"}, ("recipient.email",)),
        ({"recipient": {"email": "b@example.com"}}, ("recipient.e",)),
    ],
)
def test_holds_only_kept_arguments_rejects_anything_else(
    arguments: dict[str, JsonValue], kept: tuple[str, ...]
) -> None:
    assert not holds_only_kept_arguments(arguments, kept=kept)


@pytest.mark.parametrize(
    "kept",
    [("to", "to"), ("recipient", "recipient.email"), ("recipient.email", "recipient"), ("",), ("a..b",), ("a.",), (3,)],
)
def test_kept_paths_must_be_distinct_valid_and_apart(kept: tuple[object, ...]) -> None:
    with pytest.raises(ValueError, match="distinct, nonempty paths, none inside another"):
        validate_kept_argument_paths(kept)


def test_kept_paths_may_share_a_parent_or_a_prefix_of_letters() -> None:
    kept = ("recipient.email", "recipient.name", "to", "total")

    assert validate_kept_argument_paths(kept) == kept
