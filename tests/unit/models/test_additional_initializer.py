# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import ValidationError

from pyrit.models import AdditionalInitializer


def test_additional_initializer_defaults() -> None:
    initializer = AdditionalInitializer(initializer_name="target")

    assert initializer.parameters is None
    assert initializer.order_index is None
    assert initializer.id


def test_additional_initializer_generates_unique_ids() -> None:
    first = AdditionalInitializer(initializer_name="target")
    second = AdditionalInitializer(initializer_name="target")

    assert first.id != second.id


def test_additional_initializer_rejects_invalid_registry_name() -> None:
    with pytest.raises(ValidationError, match="Invalid registry name"):
        AdditionalInitializer(initializer_name="Not Valid")
