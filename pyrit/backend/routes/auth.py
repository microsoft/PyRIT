# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Auth configuration endpoint.

Serves non-secret Entra ID configuration to the frontend so MSAL can be
initialized without hardcoding tenant-specific values in the JS bundle.
"""

import os

from fastapi import APIRouter, Request

from pyrit.backend.middleware.auth import AuthenticatedUser

router = APIRouter()
_GRAPH_SCOPES = ["https://graph.microsoft.com/User.Read"]


@router.get("/auth/config")
async def get_auth_config_async() -> dict[str, str | bool | list[str]]:
    """
    Return Entra ID configuration for the frontend MSAL client.

    These values are non-secret (client ID, tenant ID) and are needed by
    the frontend to initialize MSAL for PKCE login. The allowed group IDs
    are included so the frontend can show appropriate error messages.

    Returns:
        dict: Auth configuration with enabled state, clientId, tenantId,
            allowedGroupIds, and delegated Microsoft Graph scopes.
    """
    client_id = os.getenv("ENTRA_CLIENT_ID", "").strip()
    tenant_id = os.getenv("ENTRA_TENANT_ID", "").strip()
    allowed_group_ids = os.getenv("ENTRA_ALLOWED_GROUP_IDS", "").strip()
    enabled = bool(client_id and tenant_id and allowed_group_ids)

    return {
        "enabled": enabled,
        "clientId": client_id,
        "tenantId": tenant_id,
        "allowedGroupIds": allowed_group_ids,
        "scopes": list(_GRAPH_SCOPES) if enabled else [],
    }


@router.get("/auth/access")
async def get_auth_access_async(request: Request) -> dict[str, bool]:
    """Return configuration-administrator access for the current user."""
    user = getattr(request.state, "user", None)
    is_admin = isinstance(user, AuthenticatedUser) and user.is_admin
    if user is None:
        is_admin = os.getenv("PYRIT_ALLOW_UNAUTHENTICATED_ADMIN", "").strip().casefold() == "true"
    return {"isAdmin": is_admin}
