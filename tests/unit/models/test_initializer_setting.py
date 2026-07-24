# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import ValidationError

from pyrit.models import InitializerSetting


def test_initializer_setting_defaults() -> None:
    setting = InitializerSetting(initializer_name="target")

    assert setting.enabled is True
    assert setting.parameters is None
    assert setting.order_index is None


def test_initializer_setting_rejects_invalid_registry_name() -> None:
    with pytest.raises(ValidationError, match="Invalid registry name"):
        InitializerSetting(initializer_name="Not Valid")
