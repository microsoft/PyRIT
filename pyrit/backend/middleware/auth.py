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
- Optionally checks group membership (handles groups overage for users in >200 groups)
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
        self._client_id = os.getenv("ENTRA_CLIENT_ID", "")
        self._allowed_group_id = os.getenv("ENTRA_ALLOWED_GROUP_ID", "")
        # OID-based allowlist: comma-separated user object IDs.
        # Used as fallback when the groups claim is unavailable.
        oids_raw = os.getenv("ENTRA_ALLOWED_OIDS", "")
        self._allowed_oids: set[str] = {o.strip() for o in oids_raw.split(",") if o.strip()}
        self._enabled = bool(self._tenant_id and self._client_id)

        if self._enabled:
            jwks_url = f"https://login.microsoftonline.com/{self._tenant_id}/discovery/v2.0/keys"
            self._jwks_client = PyJWKClient(jwks_url, cache_keys=True)
            self._issuer = f"https://login.microsoftonline.com/{self._tenant_id}/v2.0"
            logger.info("Entra ID auth middleware enabled (tenant=%s)", self._tenant_id)
            if self._allowed_oids:
                logger.info("OID allowlist active (%d entries)", len(self._allowed_oids))
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

        # Extract Bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )

        token = auth_header[7:]  # Strip "Bearer "

        # Validate JWT
        user, claims = self._validate_token(token)
        if user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
            )

        # Handle groups overage: when user is in >200 groups, Entra replaces the
        # groups array with _claim_sources containing a Graph API URL.
        if not user.groups and self._allowed_group_id and "_claim_sources" in claims:
            user.groups = await self._resolve_groups_overage_async(claims, token)

        # Authorization: check group membership or OID allowlist
        if not self._is_authorized(user):
            logger.warning(
                "User %s (%s) denied — groups=%s, allowed_group=%s, oid_allowlist=%s",
                user.email,
                user.oid,
                user.groups,
                self._allowed_group_id,
                bool(self._allowed_oids),
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "You are not authorized to access this application"},
            )

        # Attach user to request state
        request.state.user = user
        return await call_next(request)

    def _is_authorized(self, user: AuthenticatedUser) -> bool:
        """
        Check if the user is authorized via group membership or OID allowlist.

        Authorization passes if ANY of the following are true:
        - No group or OID restrictions are configured (open to all authenticated users)
        - The user's groups contain the allowed group ID
        - The user's OID is in the OID allowlist

        Args:
            user: The authenticated user extracted from the JWT.

        Returns:
            True if the user is authorized.
        """
        has_group_restriction = bool(self._allowed_group_id)
        has_oid_restriction = bool(self._allowed_oids)

        # No restrictions configured — allow all authenticated users
        if not has_group_restriction and not has_oid_restriction:
            return True

        # Check group membership
        if has_group_restriction and self._allowed_group_id in user.groups:
            return True

        # Fallback: check OID allowlist
        return has_oid_restriction and user.oid in self._allowed_oids

    async def _resolve_groups_overage_async(self, claims: dict[str, Any], token: str) -> list[str]:
        """
        Resolve group membership via Graph API when groups overage occurs.

        When a user is in >200 groups, Entra ID replaces the `groups` claim with
        `_claim_sources` containing a Graph API endpoint. This method calls the
        Microsoft Graph checkMemberObjects endpoint to verify the user is in
        the allowed group, without needing a separate Graph token.

        Args:
            claims: The decoded JWT claims containing _claim_sources.
            token: The raw Bearer token to forward to Graph API.

        Returns:
            List of group IDs the user belongs to, or empty list on failure.
        """
        try:
            # Use the overage endpoint from _claim_sources directly — it accepts
            # the original access token since the app has the user's delegated
            # permissions via the PKCE flow.
            claim_sources = claims.get("_claim_sources", {})
            src = claim_sources.get("src1", {})
            endpoint = src.get("endpoint", "")

            if not endpoint:
                logger.debug("No overage endpoint found in _claim_sources")
                return []

            # The endpoint is a legacy graph.windows.net URL that requires an
            # api-version parameter. Call it with securityEnabledOnly=true to
            # get security group memberships.
            url_with_version = f"{endpoint}?api-version=1.6"

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url_with_version,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={"securityEnabledOnly": True},
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    group_ids: list[str] = data.get("value", [])
                    logger.debug("Overage resolution returned %d group memberships", len(group_ids))
                    return group_ids
                logger.warning(
                    "Groups overage endpoint returned %d: %s",
                    response.status_code,
                    response.text[:200],
                )
                return []

        except Exception as e:
            logger.warning("Failed to resolve groups overage: %s", e)
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
