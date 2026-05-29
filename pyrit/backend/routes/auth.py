# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Auth configuration endpoint.

Serves non-secret Entra ID configuration to the frontend so MSAL can be
initialized without hardcoding tenant-specific values in the JS bundle.
"""

import os

from fastapi import APIRouter

router = APIRouter()


@router.get("/auth/config")
async def get_auth_config_async() -> dict[str, str]:
    """
    Return Entra ID configuration for the frontend MSAL client.

    These values are non-secret (client ID, tenant ID) and are needed by
    the frontend to initialize MSAL for PKCE login. The allowed group IDs
    are included so the frontend can show appropriate error messages.

    Returns:
        dict: Auth configuration with clientId, tenantId, allowedGroupIds.
    """
    return {
        "clientId": os.getenv("ENTRA_CLIENT_ID", ""),
        "tenantId": os.getenv("ENTRA_TENANT_ID", ""),
        "allowedGroupIds": os.getenv("ENTRA_ALLOWED_GROUP_IDS", ""),
    }
