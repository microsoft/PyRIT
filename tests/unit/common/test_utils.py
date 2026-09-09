# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.common.utils import combine_list, to_sha256


def test_combine_list_two_lists():
    result = combine_list(["a", "b"], ["b", "c"])
    assert result == ["a", "b", "c"]


def test_combine_list_strings():
    result = combine_list("x", "y")
    assert result == ["x", "y"]


def test_combine_list_mixed():
    result = combine_list("a", ["a", "b"])
    assert result == ["a", "b"]


def test_combine_list_preserves_first_occurrence_order():
    """Order must be stable, not whatever set() iteration happens to produce."""
    left = ["harmful", "violence", "illegal"]
    right = ["bias", "violence", "pii"]
    assert combine_list(left, right) == ["harmful", "violence", "illegal", "bias", "pii"]
    # Deterministic across repeated calls within a process too.
    assert combine_list(left, right) == combine_list(left, right)


def test_combine_list_treats_none_as_empty():
    assert combine_list(None, ["a"]) == ["a"]
    assert combine_list(["a"], None) == ["a"]
    assert combine_list(None, None) == []


def test_combine_list_duplicates_removed():
    result = combine_list(["a", "a"], ["a"])
    assert result == ["a"]


def test_to_sha256_deterministic():
    h1 = to_sha256("hello")
    h2 = to_sha256("hello")
    assert h1 == h2
    assert len(h1) == 64


def test_to_sha256_different_inputs():
    assert to_sha256("a") != to_sha256("b")


def test_to_sha256_known_value():
    import hashlib

    expected = hashlib.sha256(b"test").hexdigest()
    assert to_sha256("test") == expected
