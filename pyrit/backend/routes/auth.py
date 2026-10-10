# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Auth configuration endpoint.

Serves non-secret Entra ID configuration to the frontend so MSAL can be
initialized without hardcoding tenant-specific values in the JS bundle.
"""

from fastapi import APIRouter, Request

from pyrit.backend.middleware.auth import AuthenticatedUser, get_authentication_policy

router = APIRouter()
_GRAPH_SCOPES = ["https://graph.microsoft.com/User.Read"]


@router.get("/auth/config")
async def get_auth_config_async(request: Request) -> dict[str, str | bool | list[str]]:
    """
    Return Entra ID configuration for the frontend MSAL client.

    These values are non-secret (client ID, tenant ID) and are needed by
    the frontend to initialize MSAL for PKCE login. The allowed group IDs
    are included so the frontend can show appropriate error messages.

    Returns:
        dict: Auth configuration with enabled state, clientId, tenantId,
            allowedGroupIds, and delegated Microsoft Graph scopes.
    """
    policy = get_authentication_policy(request)

    return {
        "enabled": policy.enabled,
        "clientId": policy.client_id,
        "tenantId": policy.tenant_id,
        "allowedGroupIds": policy.allowed_group_ids_csv,
        "scopes": list(_GRAPH_SCOPES) if policy.enabled else [],
    }


@router.get("/auth/access")
async def get_auth_access_async(request: Request) -> dict[str, bool]:
    """Return configuration-administrator access for the current user."""
    user = getattr(request.state, "user", None)
    is_admin = isinstance(user, AuthenticatedUser) and user.is_admin
    if user is None:
        policy = get_authentication_policy(request)
        is_admin = policy.mode == "local" and policy.allow_unauthenticated_admin
    return {"isAdmin": is_admin}
