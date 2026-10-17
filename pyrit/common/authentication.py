# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared incoming-request authentication environment settings."""

from collections.abc import Mapping

AUTH_MODE_ENVIRONMENT_VARIABLE = "PYRIT_AUTH_MODE"
REQUIRED_ENTRA_ENVIRONMENT_VARIABLES = (
    "ENTRA_TENANT_ID",
    "ENTRA_CLIENT_ID",
    "ENTRA_ALLOWED_GROUP_IDS",
)


def should_default_to_local_authentication(*, environment: Mapping[str, str]) -> bool:
    """
    Determine whether a local launcher should explicitly select local authentication.

    Returns:
        bool: True only when no authentication intent or required Entra setting is present.
    """
    return not environment.get(AUTH_MODE_ENVIRONMENT_VARIABLE, "").strip() and not any(
        name in environment for name in REQUIRED_ENTRA_ENVIRONMENT_VARIABLES
    )
