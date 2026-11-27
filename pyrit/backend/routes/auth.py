# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Auth configuration endpoints.

Serves non-secret Entra ID configuration to the frontend so MSAL can be
initialized without hardcoding tenant-specific values in the JS bundle, and
tells MCP clients where to sign in for the MCP endpoint.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl

from pyrit.backend.authentication_policy import GRAPH_SCOPES
from pyrit.backend.middleware.auth import (
    MCP_RESOURCE_METADATA_PATH,
    AuthenticatedUser,
    get_authentication_policy,
    get_mcp_resource_url,
)

router = APIRouter()
# Served at the site root, outside /api and the OpenAPI document; clients read it before signing in.
well_known_router = APIRouter()


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
        "scopes": list(GRAPH_SCOPES) if policy.enabled else [],
    }


@well_known_router.get(MCP_RESOURCE_METADATA_PATH, include_in_schema=False)
async def get_mcp_resource_metadata_async(request: Request) -> JSONResponse:
    """
    Return the MCP endpoint's protected resource metadata (RFC 9728).

    An MCP client that gets a 401 from ``/api/mcp`` reads this document to find the Entra tenant
    and the scopes to sign in with, then sends the delegated Graph token it gets. The document is
    public, like ``/api/auth/config``, and is not cached, because it names the URL the caller used.

    Returns:
        JSONResponse: The metadata, or 404 when Entra authentication is off.
    """
    policy = get_authentication_policy(request)
    headers = {"Cache-Control": "no-store"}
    if not policy.enabled:
        return JSONResponse({"detail": "Not Found"}, status_code=404, headers=headers)
    metadata = ProtectedResourceMetadata(
        resource=AnyHttpUrl(get_mcp_resource_url(request)),
        authorization_servers=[AnyHttpUrl(f"https://login.microsoftonline.com/{policy.tenant_id}/v2.0")],
        scopes_supported=list(GRAPH_SCOPES),
        bearer_methods_supported=["header"],
        resource_name="PyRIT",
    )
    return JSONResponse(metadata.model_dump(mode="json", exclude_none=True), headers=headers)


@router.get("/auth/access")
async def get_auth_access_async(request: Request) -> dict[str, bool]:
    """Return configuration-administrator access for the current user."""
    user = getattr(request.state, "user", None)
    is_admin = isinstance(user, AuthenticatedUser) and user.is_admin
    if user is None:
        policy = get_authentication_policy(request)
        is_admin = policy.mode == "local" and policy.allow_unauthenticated_admin
    return {"isAdmin": is_admin}
