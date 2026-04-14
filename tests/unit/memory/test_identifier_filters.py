# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.identifiers.identifier_filters import IdentifierFilter, IdentifierType
from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import AttackResultEntry


@pytest.mark.parametrize(
    "array_element_path, partial_match, case_sensitive",
    [
        ("$.class_name", True, False),
        ("$.class_name", False, True),
        ("$.class_name", True, True),
    ],
    ids=["array_element_path+partial_match", "array_element_path+case_sensitive", "array_element_path+both"],
)
def test_identifier_filter_array_element_path_with_partial_or_case_sensitive_raises(
    array_element_path: str, partial_match: bool, case_sensitive: bool
):
    with pytest.raises(ValueError, match="Cannot use array_element_path with partial_match or case_sensitive"):
        IdentifierFilter(
            identifier_type=IdentifierType.ATTACK,
            property_path="$.children",
            value="test",
            array_element_path=array_element_path,
            partial_match=partial_match,
            case_sensitive=case_sensitive,
        )


def test_identifier_filter_valid_with_array_element_path():
    f = IdentifierFilter(
        identifier_type=IdentifierType.CONVERTER,
        property_path="$",
        value="Base64Converter",
        array_element_path="$.class_name",
    )
    assert f.array_element_path == "$.class_name"
    assert not f.partial_match
    assert not f.case_sensitive


def test_build_identifier_filter_conditions_unsupported_type_raises(sqlite_instance: MemoryInterface):
    filters = [
        IdentifierFilter(
            identifier_type=IdentifierType.SCORER,
            property_path="$.class_name",
            value="MyScorer",
        )
    ]
    with pytest.raises(ValueError, match="does not support identifier type"):
        sqlite_instance._build_identifier_filter_conditions(
            identifier_filters=filters,
            identifier_column_map={IdentifierType.ATTACK: AttackResultEntry.atomic_attack_identifier},
            caller="test_caller",
        )
