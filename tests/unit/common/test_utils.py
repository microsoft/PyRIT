# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.common.utils import combine_list, to_sha256


def test_combine_list_two_lists():
    result = combine_list(["a", "b"], ["b", "c"])
    assert set(result) == {"a", "b", "c"}


def test_combine_list_strings():
    result = combine_list("x", "y")
    assert set(result) == {"x", "y"}


def test_combine_list_mixed():
    result = combine_list("a", ["a", "b"])
    assert set(result) == {"a", "b"}


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
