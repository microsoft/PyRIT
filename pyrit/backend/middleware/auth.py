# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Entra ID JWT validation middleware for FastAPI.

Validates Bearer tokens from the Authorization header against Entra ID JWKS.
Uses PKCE (public client) flow — no client secrets needed.

The middleware:
- Skips auth for health check and auth config endpoints
- Validates JWT signature against Entra ID's JWKS endpoint
- Verifies issuer, audience, and expiration
- Optionally checks group membership (resolves via Graph API if user is in >200 groups)
- Attaches user info to request.state for use by route handlers
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Paths that bypass authentication
_PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/config",
    "/api/media",
}


@dataclass
class AuthenticatedUser:
    """User identity extracted from a validated JWT."""

    oid: str
    name: str
    email: str
    groups: list[str]


class EntraAuthMiddleware(BaseHTTPMiddleware):
    """Validate Entra ID JWTs on every request (except public paths)."""

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the middleware with Entra ID configuration from environment variables."""
        super().__init__(app)
        self._tenant_id = os.getenv("ENTRA_TENANT_ID", "")
        # _client_id is used as the expected JWT audience in _validate_token
        self._client_id = os.getenv("ENTRA_CLIENT_ID", "")
        groups_raw = os.getenv("ENTRA_ALLOWED_GROUP_IDS", "")
        self._allowed_group_ids: set[str] = {g.strip() for g in groups_raw.split(",") if g.strip()}
        self._enabled = bool(self._tenant_id and self._client_id)

        self._jwks_client: PyJWKClient | None
        if self._enabled:
            jwks_url = f"https://login.microsoftonline.com/{self._tenant_id}/discovery/v2.0/keys"
            self._jwks_client = PyJWKClient(jwks_url, cache_keys=True)
            self._issuer = f"https://login.microsoftonline.com/{self._tenant_id}/v2.0"
            logger.info("Entra ID auth middleware enabled (tenant=%s)", self._tenant_id)
        else:
            self._jwks_client = None
            self._issuer = ""
            logger.warning(
                "Entra ID auth middleware DISABLED — ENTRA_TENANT_ID or ENTRA_CLIENT_ID not set. "
                "All requests will be allowed without authentication."
            )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """
        Validate the Bearer token and attach user info to request.state.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware / route handler.

        Returns:
            Response with 401 if auth fails, otherwise the normal response.
        """
        # Skip auth for public paths and static files
        path = request.url.path
        if not self._enabled or path in _PUBLIC_PATHS or not path.startswith("/api"):
            return await call_next(request)

        result = await self._authenticate_request_async(request)
        if isinstance(result, JSONResponse):
            return result  # Authentication failed with 401 or 403

        request.state.user = result
        return await call_next(request)

    async def _authenticate_request_async(self, request: Request) -> AuthenticatedUser | JSONResponse:
        """
        Extract, validate, and authorize the Bearer token from the request.

        Returns:
            AuthenticatedUser if validation and authorization succeed,
            JSONResponse with 401 or 403 if they fail.
        """
        # Extract Bearer token from Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )

        token = auth_header.removeprefix("Bearer ")

        # Validate the token and extract user info and claims
        user, claims = self._validate_token(token)
        if user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
            )

        # NOTE: This fallback is currently non-functional. The access token's
        # audience is this app's client ID, but Graph requires tokens with
        # aud=https://graph.microsoft.com. The call will return 401 and the
        # user will be denied. This will be resolved when the frontend
        # migrates from the custom `access` scope to Graph's User.Read scope.
        if not user.groups and self._allowed_group_ids and "_claim_sources" in claims:
            user.groups = await self._resolve_excess_groups_async(claims, token)

        # Authorize the user based on group membership
        if not self._is_authorized(user):
            logger.warning(
                "User %s (%s) denied — groups=%s, allowed_groups=%s",
                user.email,
                user.oid,
                user.groups,
                self._allowed_group_ids,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "You are not authorized to access this application"},
            )

        return user

    def _is_authorized(self, user: AuthenticatedUser) -> bool:
        """
        Check if the user is authorized via group membership.

        Authorization passes if:
        - No group restrictions are configured (open to all authenticated users)
        - The user's groups intersect with the allowed group IDs

        Returns:
            True if the user is authorized, False otherwise.
        """
        if not self._allowed_group_ids:
            return True
        return bool(self._allowed_group_ids & set(user.groups))

    async def _resolve_excess_groups_async(self, claims: dict[str, Any], token: str) -> list[str]:
        """
        Resolve group membership via Microsoft Graph when user is in >200 groups.

        When a user is in >200 groups, Entra ID replaces the `groups` claim with
        `_claim_sources` containing a Graph API endpoint. This method calls the
        Microsoft Graph `getMemberObjects` endpoint to retrieve transitive group
        memberships, using the user's access token.

        Args:
            claims: The decoded JWT claims containing _claim_sources.
            token: The raw Bearer token to forward to Graph API.

        Returns:
            List of group IDs the user belongs to, or empty list on failure.
        """
        try:
            claim_sources = claims.get("_claim_sources", {})
            src = claim_sources.get("src1", {})
            endpoint = src.get("endpoint", "")

            if not endpoint:
                logger.debug("No group resolution endpoint found in _claim_sources")
                return []

            # The _claim_sources endpoint may be a legacy graph.windows.net URL.
            # Rewrite to Microsoft Graph (graph.microsoft.com) which is the
            # supported API. The legacy Azure AD Graph was retired in 2023.
            if "graph.windows.net" in endpoint:
                # Legacy format: https://graph.windows.net/{tenant}/users/{oid}/getMemberObjects
                # Graph format:  https://graph.microsoft.com/v1.0/me/getMemberObjects
                endpoint = "https://graph.microsoft.com/v1.0/me/getMemberObjects"

            all_group_ids: list[str] = []
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={"securityEnabledOnly": True},
                    timeout=10.0,
                )

                if response.status_code != 200:
                    logger.warning(
                        "Group resolution endpoint returned %d: %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return []

                data = response.json()
                all_group_ids.extend(data.get("value", []))

                # Handle pagination — Graph may return @odata.nextLink for large results
                next_link = data.get("@odata.nextLink")
                while next_link:
                    response = await client.get(
                        next_link,
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=10.0,
                    )
                    if response.status_code != 200:
                        logger.warning("Group resolution pagination failed at %s: %d", next_link, response.status_code)
                        break
                    data = response.json()
                    all_group_ids.extend(data.get("value", []))
                    next_link = data.get("@odata.nextLink")

            logger.debug("Group resolution returned %d group memberships", len(all_group_ids))
            return all_group_ids

        except Exception as e:
            logger.warning("Failed to resolve group memberships: %s", e)
            return []

    def _validate_token(self, token: str) -> tuple[Optional[AuthenticatedUser], dict[str, Any]]:
        """
        Validate a JWT against Entra ID JWKS.

        Args:
            token: The raw JWT string.

        Returns:
            Tuple of (AuthenticatedUser, claims) if valid, (None, {}) if validation fails.
        """
        try:
            if self._jwks_client is None:
                raise RuntimeError("JWKS client not initialized")
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
            user = AuthenticatedUser(
                oid=claims.get("oid", claims.get("sub", "")),
                name=claims.get("name", ""),
                email=claims.get("preferred_username", claims.get("email", "")),
                groups=claims.get("groups", []),
            )
            return user, claims
        except jwt.ExpiredSignatureError:
            logger.debug("Token expired")
            return None, {}
        except jwt.InvalidTokenError as e:
            logger.debug("Token validation failed: %s", e)
            return None, {}
        except Exception as e:
            logger.warning("Unexpected error during token validation: %s", e)
            return None, {}
