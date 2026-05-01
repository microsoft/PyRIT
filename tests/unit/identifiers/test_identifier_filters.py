# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.identifiers.identifier_filters import IdentifierFilter, IdentifierType

# --- IdentifierType enum ---


def test_identifier_type_values():
    assert IdentifierType.ATTACK.value == "attack"
    assert IdentifierType.TARGET.value == "target"
    assert IdentifierType.SCORER.value == "scorer"
    assert IdentifierType.CONVERTER.value == "converter"


def test_identifier_type_member_count():
    assert len(IdentifierType) == 4


# --- IdentifierFilter creation ---


def test_identifier_filter_defaults():
    f = IdentifierFilter(identifier_type=IdentifierType.TARGET, property_path="$.name", value="openai")
    assert f.identifier_type == IdentifierType.TARGET
    assert f.property_path == "$.name"
    assert f.value == "openai"
    assert f.array_element_path is None
    assert f.partial_match is False
    assert f.case_sensitive is False


def test_identifier_filter_with_partial_match():
    f = IdentifierFilter(
        identifier_type=IdentifierType.SCORER,
        property_path="$.class_name",
        value="Refusal",
        partial_match=True,
    )
    assert f.partial_match is True


def test_identifier_filter_with_case_sensitive():
    f = IdentifierFilter(
        identifier_type=IdentifierType.CONVERTER,
        property_path="$.class_name",
        value="Base64",
        case_sensitive=True,
    )
    assert f.case_sensitive is True


def test_identifier_filter_with_array_element_path():
    f = IdentifierFilter(
        identifier_type=IdentifierType.ATTACK,
        property_path="$.converters",
        value="Base64Converter",
        array_element_path="$.class_name",
    )
    assert f.array_element_path == "$.class_name"


# --- IdentifierFilter validation ---


def test_identifier_filter_raises_array_element_path_with_partial_match():
    with pytest.raises(ValueError, match="Cannot use array_element_path with partial_match"):
        IdentifierFilter(
            identifier_type=IdentifierType.TARGET,
            property_path="$.items",
            value="test",
            array_element_path="$.name",
            partial_match=True,
        )


def test_identifier_filter_raises_array_element_path_with_case_sensitive():
    with pytest.raises(ValueError, match="Cannot use array_element_path with partial_match or case_sensitive"):
        IdentifierFilter(
            identifier_type=IdentifierType.TARGET,
            property_path="$.items",
            value="test",
            array_element_path="$.name",
            case_sensitive=True,
        )


def test_identifier_filter_raises_partial_match_with_case_sensitive():
    with pytest.raises(ValueError, match="case_sensitive is not reliably supported with partial_match"):
        IdentifierFilter(
            identifier_type=IdentifierType.TARGET,
            property_path="$.name",
            value="test",
            partial_match=True,
            case_sensitive=True,
        )


# --- Frozen dataclass behavior ---


def test_identifier_filter_is_frozen():
    f = IdentifierFilter(identifier_type=IdentifierType.TARGET, property_path="$.name", value="x")
    with pytest.raises(AttributeError):
        f.value = "y"


def test_identifier_filter_equality():
    f1 = IdentifierFilter(identifier_type=IdentifierType.TARGET, property_path="$.name", value="x")
    f2 = IdentifierFilter(identifier_type=IdentifierType.TARGET, property_path="$.name", value="x")
    assert f1 == f2


def test_identifier_filter_inequality():
    f1 = IdentifierFilter(identifier_type=IdentifierType.TARGET, property_path="$.name", value="x")
    f2 = IdentifierFilter(identifier_type=IdentifierType.TARGET, property_path="$.name", value="y")
    assert f1 != f2


def test_identifier_filter_hashable():
    f = IdentifierFilter(identifier_type=IdentifierType.TARGET, property_path="$.name", value="x")
    s = {f}
    assert f in s
