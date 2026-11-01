# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re

import pytest

from pyrit.models import DEFAULT_FINDING_FIELDS, FindingFields
from pyrit.setup.finding_fields import (
    get_active_finding_fields,
    reset_active_finding_fields,
    set_active_finding_fields,
)

pytestmark = pytest.mark.usefixtures("reset_finding_fields")


def test_get_active_finding_fields_returns_the_default_until_set() -> None:
    assert get_active_finding_fields() is DEFAULT_FINDING_FIELDS


def test_set_active_finding_fields_replaces_the_default(custom_finding_fields: FindingFields) -> None:
    set_active_finding_fields(finding_fields=custom_finding_fields, source="First")
    assert get_active_finding_fields() is custom_finding_fields


def test_set_active_finding_fields_twice_names_both_sources(custom_finding_fields: FindingFields) -> None:
    set_active_finding_fields(finding_fields=custom_finding_fields, source="First")
    with pytest.raises(ValueError, match="Second cannot set the finding fields: First already set them"):
        set_active_finding_fields(finding_fields=DEFAULT_FINDING_FIELDS, source="Second")
    assert get_active_finding_fields() is custom_finding_fields


def test_set_active_finding_fields_rejects_a_value_that_is_not_finding_fields() -> None:
    with pytest.raises(TypeError, match=re.escape("Contoso must set FindingFields, not dict.")):
        set_active_finding_fields(finding_fields={}, source="Contoso")
    assert get_active_finding_fields() is DEFAULT_FINDING_FIELDS


def test_reset_restores_the_default(custom_finding_fields: FindingFields) -> None:
    set_active_finding_fields(finding_fields=custom_finding_fields, source="First")
    reset_active_finding_fields()
    assert get_active_finding_fields() is DEFAULT_FINDING_FIELDS
