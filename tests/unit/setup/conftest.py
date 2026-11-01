# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.models import FindingField, FindingFields


@pytest.fixture
def custom_finding_fields() -> FindingFields:
    """Finding fields with one severity option, unlike ``DEFAULT_FINDING_FIELDS``."""
    return FindingFields(
        severity=FindingField(key="severity", label="Severity", type="choice", options=[("high", "High")])
    )
