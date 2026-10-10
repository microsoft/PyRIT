# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for process-start incoming-request authentication policy."""

from collections.abc import Mapping

import pytest

from pyrit.backend.authentication_policy import (
    AuthenticationConfigurationError,
    resolve_authentication_policy,
)


def _entra_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "PYRIT_AUTH_MODE": "entra",
        "ENTRA_TENANT_ID": "tenant-id",
        "ENTRA_CLIENT_ID": "client-id",
        "ENTRA_ALLOWED_GROUP_IDS": "group-1, group-2",
    }
    environment.update(overrides)
    return environment


def test_resolve_authentication_policy_local_mode() -> None:
    policy = resolve_authentication_policy(environment={"PYRIT_AUTH_MODE": " local "})

    assert policy.mode == "local"
    assert not policy.enabled
    assert policy.allowed_group_ids == ()


def test_resolve_authentication_policy_local_admin_override() -> None:
    policy = resolve_authentication_policy(
        environment={
            "PYRIT_AUTH_MODE": "local",
            "PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "TRUE",
        }
    )

    assert policy.allow_unauthenticated_admin


def test_resolve_authentication_policy_explicit_entra_mode() -> None:
    policy = resolve_authentication_policy(
        environment=_entra_environment(
            ENTRA_ALLOWED_GROUP_IDS=" group-2,group-1,group-2 ",
            ENTRA_ADMIN_GROUP_ID=" admin-group ",
        )
    )

    assert policy.mode == "entra"
    assert policy.enabled
    assert policy.tenant_id == "tenant-id"
    assert policy.client_id == "client-id"
    assert policy.allowed_group_ids == ("group-2", "group-1")
    assert policy.allowed_group_ids_csv == "group-2,group-1"
    assert policy.admin_group_id == "admin-group"


def test_resolve_authentication_policy_infers_entra_from_complete_configuration() -> None:
    environment = _entra_environment()
    del environment["PYRIT_AUTH_MODE"]

    policy = resolve_authentication_policy(environment=environment)

    assert policy.mode == "entra"


@pytest.mark.parametrize("environment", [{}, {"UNRELATED_SETTING": "value"}])
def test_resolve_authentication_policy_rejects_absent_configuration(environment: Mapping[str, str]) -> None:
    with pytest.raises(AuthenticationConfigurationError, match="Authentication configuration is absent"):
        resolve_authentication_policy(environment=environment)


@pytest.mark.parametrize(
    "environment, missing_name",
    [
        ({"ENTRA_TENANT_ID": "tenant-id"}, "ENTRA_CLIENT_ID"),
        (_entra_environment(ENTRA_TENANT_ID=" "), "ENTRA_TENANT_ID"),
        (_entra_environment(ENTRA_CLIENT_ID=""), "ENTRA_CLIENT_ID"),
        (_entra_environment(ENTRA_ALLOWED_GROUP_IDS=" , "), "ENTRA_ALLOWED_GROUP_IDS"),
    ],
)
def test_resolve_authentication_policy_rejects_incomplete_entra_configuration(
    environment: Mapping[str, str], missing_name: str
) -> None:
    with pytest.raises(AuthenticationConfigurationError, match=missing_name):
        resolve_authentication_policy(environment=environment)


@pytest.mark.parametrize("mode", ["disabled", "optional", "true"])
def test_resolve_authentication_policy_rejects_unknown_mode(mode: str) -> None:
    with pytest.raises(AuthenticationConfigurationError, match="must be 'local' or 'entra'"):
        resolve_authentication_policy(environment={"PYRIT_AUTH_MODE": mode})


@pytest.mark.parametrize(
    "name, value",
    [
        ("ENTRA_TENANT_ID", ""),
        ("ENTRA_CLIENT_ID", "client-id"),
        ("ENTRA_ALLOWED_GROUP_IDS", "group-id"),
        ("ENTRA_ADMIN_GROUP_ID", "admin-id"),
    ],
)
def test_resolve_authentication_policy_rejects_entra_settings_in_local_mode(name: str, value: str) -> None:
    with pytest.raises(AuthenticationConfigurationError, match="cannot be combined"):
        resolve_authentication_policy(environment={"PYRIT_AUTH_MODE": "local", name: value})


def test_resolve_authentication_policy_rejects_entra_admin_override() -> None:
    environment = _entra_environment(PYRIT_ALLOW_UNAUTHENTICATED_ADMIN="true")

    with pytest.raises(AuthenticationConfigurationError, match="not allowed"):
        resolve_authentication_policy(environment=environment)


def test_resolve_authentication_policy_rejects_invalid_boolean() -> None:
    with pytest.raises(AuthenticationConfigurationError, match="must be 'true' or 'false'"):
        resolve_authentication_policy(
            environment={
                "PYRIT_AUTH_MODE": "local",
                "PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "yes",
            }
        )
