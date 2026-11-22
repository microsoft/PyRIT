# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""CLI factories and context managers annotated ``-> Self`` keep the subclass type."""

from typing import assert_type
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.cli._auth import BackendAuthConfig
from pyrit.cli.api_client import PyRITApiClient


class _DerivedBackendAuthConfig(BackendAuthConfig):
    pass


class _DerivedApiClient(PyRITApiClient):
    pass


@pytest.mark.parametrize("config_cls", [BackendAuthConfig, _DerivedBackendAuthConfig])
@pytest.mark.parametrize(
    "payload",
    [
        {"enabled": False},
        {"tenantId": "", "clientId": ""},
        {"enabled": True, "tenantId": "t", "clientId": "c", "scopes": ["https://graph.microsoft.com/User.Read"]},
    ],
    ids=["disabled", "legacy-disabled", "enabled"],
)
def test_backend_auth_config_from_payload_returns_calling_class(
    config_cls: type[BackendAuthConfig], payload: dict[str, object]
) -> None:
    config = config_cls.from_payload(payload)

    assert type(config) is config_cls


def test_backend_auth_config_from_payload_preserves_subclass_type() -> None:
    config = _DerivedBackendAuthConfig.from_payload({"enabled": False})

    assert_type(config, _DerivedBackendAuthConfig)
    assert isinstance(config, _DerivedBackendAuthConfig)


async def test_api_client_aenter_preserves_subclass_type() -> None:
    client = _DerivedApiClient(base_url="http://localhost:8000")
    with (
        patch("pyrit._compatibility.get_compatibility_id", return_value="build"),
        patch.object(_DerivedApiClient, "_verify_compatibility_async", new=AsyncMock()),
    ):
        async with client as entered:
            assert_type(entered, _DerivedApiClient)
            assert entered is client
