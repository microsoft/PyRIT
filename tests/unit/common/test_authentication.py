# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for shared authentication environment settings."""

from collections.abc import Mapping

import pytest

from pyrit.common.authentication import should_default_to_local_authentication


@pytest.mark.parametrize(
    "environment",
    [
        {"PYRIT_AUTH_MODE": "entra"},
        {"ENTRA_TENANT_ID": "tenant"},
        {"ENTRA_CLIENT_ID": ""},
        {"ENTRA_ALLOWED_GROUP_IDS": "group"},
    ],
)
def test_should_default_to_local_authentication_preserves_auth_configuration(
    environment: Mapping[str, str],
) -> None:
    assert not should_default_to_local_authentication(environment=environment)


def test_should_default_to_local_authentication_accepts_unconfigured_environment() -> None:
    assert should_default_to_local_authentication(environment={"UNRELATED": "value"})
