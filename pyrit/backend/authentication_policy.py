# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Immutable incoming-request authentication policy."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

AuthenticationMode = Literal["local", "entra"]

_AUTH_MODE_VARIABLE = "PYRIT_AUTH_MODE"
_REQUIRED_ENTRA_VARIABLES = (
    "ENTRA_TENANT_ID",
    "ENTRA_CLIENT_ID",
    "ENTRA_ALLOWED_GROUP_IDS",
)
_ENTRA_VARIABLES = (*_REQUIRED_ENTRA_VARIABLES, "ENTRA_ADMIN_GROUP_ID")
_UNAUTHENTICATED_ADMIN_VARIABLE = "PYRIT_ALLOW_UNAUTHENTICATED_ADMIN"


class AuthenticationConfigurationError(ValueError):
    """Authentication settings cannot produce a safe startup policy."""


@dataclass(frozen=True)
class AuthenticationPolicy:
    """Validated process-start policy for incoming GUI requests."""

    mode: AuthenticationMode
    tenant_id: str = ""
    client_id: str = ""
    allowed_group_ids: tuple[str, ...] = ()
    admin_group_id: str = ""
    allow_unauthenticated_admin: bool = False

    @property
    def enabled(self) -> bool:
        """Whether Microsoft Entra authentication protects ordinary API requests."""
        return self.mode == "entra"

    @property
    def allowed_group_ids_csv(self) -> str:
        """Normalized group IDs for the frontend bootstrap contract."""
        return ",".join(self.allowed_group_ids)


def _parse_boolean_setting(*, environment: Mapping[str, str], name: str) -> bool:
    raw_value = environment.get(name, "").strip().casefold()
    if not raw_value:
        return False
    if raw_value == "true":
        return True
    if raw_value == "false":
        return False
    raise AuthenticationConfigurationError(f"{name} must be 'true' or 'false' when set.")


def _parse_allowed_group_ids(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(group_id.strip() for group_id in value.split(",") if group_id.strip()))


def resolve_authentication_policy(*, environment: Mapping[str, str] | None = None) -> AuthenticationPolicy:
    """
    Resolve and validate the process-start authentication policy.

    Args:
        environment: Process environment to validate. Defaults to ``os.environ``.

    Returns:
        AuthenticationPolicy: Immutable validated authentication policy.

    Raises:
        AuthenticationConfigurationError: If settings are missing, incomplete,
            contradictory, or unsupported.
    """
    environment = os.environ if environment is None else environment
    mode_raw = environment.get(_AUTH_MODE_VARIABLE, "").strip().casefold()
    if mode_raw and mode_raw not in {"local", "entra"}:
        raise AuthenticationConfigurationError(
            f"{_AUTH_MODE_VARIABLE} must be 'local' or 'entra'; received {mode_raw!r}."
        )

    tenant_id = environment.get("ENTRA_TENANT_ID", "").strip()
    client_id = environment.get("ENTRA_CLIENT_ID", "").strip()
    allowed_group_ids = _parse_allowed_group_ids(environment.get("ENTRA_ALLOWED_GROUP_IDS", ""))
    admin_group_id = environment.get("ENTRA_ADMIN_GROUP_ID", "").strip()
    allow_unauthenticated_admin = _parse_boolean_setting(
        environment=environment,
        name=_UNAUTHENTICATED_ADMIN_VARIABLE,
    )

    if mode_raw == "local":
        configured_entra_variables = [name for name in _ENTRA_VARIABLES if name in environment]
        if configured_entra_variables:
            joined_names = ", ".join(configured_entra_variables)
            raise AuthenticationConfigurationError(
                f"{_AUTH_MODE_VARIABLE}=local cannot be combined with Entra settings: {joined_names}."
            )
        return AuthenticationPolicy(
            mode="local",
            allow_unauthenticated_admin=allow_unauthenticated_admin,
        )

    required_values = {
        "ENTRA_TENANT_ID": tenant_id,
        "ENTRA_CLIENT_ID": client_id,
        "ENTRA_ALLOWED_GROUP_IDS": allowed_group_ids,
    }
    missing_settings = [name for name, value in required_values.items() if not value]
    if missing_settings:
        if not mode_raw and not any(name in environment for name in _REQUIRED_ENTRA_VARIABLES):
            raise AuthenticationConfigurationError(
                "Authentication configuration is absent. For loopback-only local development, "
                f"set {_AUTH_MODE_VARIABLE}=local. For a shared deployment, set "
                f"{_AUTH_MODE_VARIABLE}=entra and configure {', '.join(_REQUIRED_ENTRA_VARIABLES)}."
            )
        raise AuthenticationConfigurationError(
            f"Incomplete Entra ID configuration: {', '.join(missing_settings)} must be set."
        )
    if allow_unauthenticated_admin:
        raise AuthenticationConfigurationError(
            f"{_UNAUTHENTICATED_ADMIN_VARIABLE}=true is not allowed when Entra authentication is enabled."
        )

    return AuthenticationPolicy(
        mode="entra",
        tenant_id=tenant_id,
        client_id=client_id,
        allowed_group_ids=allowed_group_ids,
        admin_group_id=admin_group_id,
    )
