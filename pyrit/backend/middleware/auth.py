# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Microsoft Graph-backed authentication middleware for FastAPI.

The frontend acquires a delegated Graph access token using PKCE. This middleware
forwards that opaque token only to trusted Microsoft Graph endpoints to retrieve
the current user's identity and group memberships, then applies local group-based
authorization.
"""

import logging
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import Any, ClassVar

import httpx
from fastapi import HTTPException, status
from starlette._utils import get_route_path
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from pyrit.backend.authentication_policy import AuthenticationPolicy

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Expected authentication or authorization failure."""

    def __init__(self, *, status_code: int, detail: str) -> None:
        """Initialize an authentication failure with its HTTP representation."""
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class AuthenticatedUser:
    """User identity returned by Microsoft Graph."""

    oid: str
    name: str
    email: str
    groups: list[str]
    is_admin: bool = False


def get_authentication_policy(request: Request) -> AuthenticationPolicy:
    """Return the validated process-start authentication policy."""
    state = getattr(request.scope.get("app"), "state", None)
    policy = getattr(state, "authentication_policy", None)
    if not isinstance(policy, AuthenticationPolicy):
        raise RuntimeError("Authentication policy was not initialized during application startup.")
    return policy


def require_admin(request: Request) -> None:
    """Require an administrator when authentication is enabled."""
    user = getattr(request.state, "user", None)
    if isinstance(user, AuthenticatedUser) and user.is_admin:
        return
    if user is None:
        policy = get_authentication_policy(request)
        if policy.mode == "local" and policy.allow_unauthenticated_admin:
            return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrator access is required",
    )


class EntraAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate API requests through Microsoft Graph."""

    # Paths that bypass authentication
    _PUBLIC_PATHS: ClassVar[set[str]] = {
        "/api/health",
        "/api/auth/config",
        "/api/media",
    }

    _GRAPH_ME_URL: ClassVar[str] = "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName"
    _GRAPH_CHECK_MEMBER_GROUPS_URL: ClassVar[str] = "https://graph.microsoft.com/v1.0/me/checkMemberGroups"
    _GRAPH_TIMEOUT_SECONDS: ClassVar[float] = 10.0
    _GRAPH_MAX_GROUP_IDS_PER_REQUEST: ClassVar[int] = 20
    _AUTH_CACHE_TTL_SECONDS: ClassVar[float] = 60.0
    _AUTH_CACHE_MAX_ENTRIES: ClassVar[int] = 256

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the middleware cache; policy is supplied by application startup."""
        super().__init__(app)
        self._auth_cache: OrderedDict[str, tuple[float, AuthenticatedUser]] = OrderedDict()

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
        path = get_route_path(request.scope)
        policy = get_authentication_policy(request)
        if policy.mode == "local" or path in self._PUBLIC_PATHS or not path.startswith("/api"):
            return await call_next(request)

        try:
            user = await self._authenticate_request_async(request, policy=policy)
        except AuthenticationError as error:
            return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

        request.state.user = user
        return await call_next(request)

    async def _authenticate_request_async(self, request: Request, *, policy: AuthenticationPolicy) -> AuthenticatedUser:
        """
        Extract, validate, and authorize the Bearer token from the request.

        Returns:
            AuthenticatedUser: The authenticated and authorized user.

        Raises:
            AuthenticationError: If authentication or authorization fails.
        """
        auth_header = request.headers.get("Authorization", "")
        auth_parts = auth_header.split()
        if len(auth_parts) != 2 or auth_parts[0].casefold() != "bearer" or not auth_parts[1]:
            raise AuthenticationError(
                status_code=401,
                detail="Missing or invalid Authorization header",
            )

        token = auth_parts[1]
        cache_key = sha256(token.encode("utf-8")).hexdigest()
        cached_user = self._get_cached_user(cache_key=cache_key)
        if cached_user is not None:
            return cached_user

        user = await self._authenticate_with_graph_async(token=token, policy=policy)

        # Authorize the user based on group membership
        if not self._is_authorized(user, policy=policy):
            logger.warning(
                "User %s (%s) denied — groups=%s, allowed_groups=%s",
                user.email,
                user.oid,
                user.groups,
                policy.allowed_group_ids,
            )
            raise AuthenticationError(
                status_code=403,
                detail="You are not authorized to access this application",
            )

        self._cache_user(cache_key=cache_key, user=user)
        return user

    def _get_cached_user(self, *, cache_key: str) -> AuthenticatedUser | None:
        cached = self._auth_cache.get(cache_key)
        if cached is None:
            return None

        expires_at, user = cached
        if expires_at <= monotonic():
            del self._auth_cache[cache_key]
            return None

        self._auth_cache.move_to_end(cache_key)
        return AuthenticatedUser(
            oid=user.oid,
            name=user.name,
            email=user.email,
            groups=list(user.groups),
            is_admin=user.is_admin,
        )

    def _cache_user(self, *, cache_key: str, user: AuthenticatedUser) -> None:
        cached_user = AuthenticatedUser(
            oid=user.oid,
            name=user.name,
            email=user.email,
            groups=list(user.groups),
            is_admin=user.is_admin,
        )
        self._auth_cache[cache_key] = (monotonic() + self._AUTH_CACHE_TTL_SECONDS, cached_user)
        self._auth_cache.move_to_end(cache_key)
        while len(self._auth_cache) > self._AUTH_CACHE_MAX_ENTRIES:
            self._auth_cache.popitem(last=False)

    async def _authenticate_with_graph_async(self, *, token: str, policy: AuthenticationPolicy) -> AuthenticatedUser:
        """
        Authenticate a delegated access token and retrieve the current user.

        Args:
            token (str): The opaque Microsoft Graph access token.
            policy: Validated process-start authentication policy.

        Returns:
            AuthenticatedUser: The user returned by Graph.

        Raises:
            AuthenticationError: If Graph rejects the token or cannot authenticate the user.
        """
        try:
            async with httpx.AsyncClient() as client:
                profile_response = await client.get(
                    self._GRAPH_ME_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self._GRAPH_TIMEOUT_SECONDS,
                )
                if profile_response.status_code != 200:
                    raise self._graph_error(
                        status_code=profile_response.status_code,
                        operation="user profile lookup",
                    )

                profile = profile_response.json()
                if not isinstance(profile, dict):
                    raise ValueError("Microsoft Graph returned invalid user profile data")
                user = self._user_from_graph_profile(profile)
                if user is None:
                    logger.warning("Microsoft Graph returned an invalid user profile")
                    raise self._graph_unavailable_error()

                groups = await self._check_group_memberships_async(client=client, token=token, policy=policy)
                user.groups = groups
                user.is_admin = policy.admin_group_id in groups

                return user
        except (httpx.RequestError, ValueError) as error:
            logger.warning("Microsoft Graph authentication failed: %s", type(error).__name__)
            raise self._graph_unavailable_error() from error

    def _is_authorized(self, user: AuthenticatedUser, *, policy: AuthenticationPolicy) -> bool:
        """
        Check if the user is authorized via group membership.

        Returns:
            True if the user's groups intersect with the allowed group IDs, False otherwise.
        """
        authorized_group_ids = set(policy.allowed_group_ids) | {policy.admin_group_id}
        authorized_group_ids.discard("")
        return bool(authorized_group_ids & set(user.groups))

    async def _check_group_memberships_async(
        self, *, client: httpx.AsyncClient, token: str, policy: AuthenticationPolicy
    ) -> list[str]:
        """
        Check which allowed groups contain the current user through Graph.

        Args:
            client (httpx.AsyncClient): The asynchronous Graph HTTP client.
            token (str): The opaque Graph access token.
            policy: Validated process-start authentication policy.

        Returns:
            list[str]: The allowed group IDs containing the current user.

        Raises:
            AuthenticationError: If Graph rejects or cannot complete the lookup.
        """
        matched_group_ids: list[str] = []
        allowed_group_ids = set(policy.allowed_group_ids) | {policy.admin_group_id}
        allowed_group_ids.discard("")
        sorted_group_ids = sorted(allowed_group_ids)
        for offset in range(0, len(sorted_group_ids), self._GRAPH_MAX_GROUP_IDS_PER_REQUEST):
            group_ids = sorted_group_ids[offset : offset + self._GRAPH_MAX_GROUP_IDS_PER_REQUEST]
            response = await client.post(
                self._GRAPH_CHECK_MEMBER_GROUPS_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"groupIds": group_ids},
                timeout=self._GRAPH_TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                raise self._graph_error(
                    status_code=response.status_code,
                    operation="group membership check",
                )

            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Microsoft Graph returned invalid group membership data")
            values = data.get("value")
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise ValueError("Microsoft Graph returned invalid group membership data")
            matched_group_ids.extend(values)

        logger.debug("Group membership check matched %d allowed groups", len(matched_group_ids))
        return matched_group_ids

    def _user_from_graph_profile(self, profile: dict[str, Any]) -> AuthenticatedUser | None:
        """
        Convert a Microsoft Graph profile into an authenticated user.

        Args:
            profile (dict[str, Any]): The response from the Graph ``/me`` endpoint.

        Returns:
            AuthenticatedUser | None: The authenticated user, or None for invalid data.
        """
        oid = profile.get("id")
        if not isinstance(oid, str) or not oid:
            return None

        display_name = profile.get("displayName")
        mail = profile.get("mail")
        principal_name = profile.get("userPrincipalName")
        return AuthenticatedUser(
            oid=oid,
            name=display_name if isinstance(display_name, str) else "",
            email=mail if isinstance(mail, str) and mail else principal_name if isinstance(principal_name, str) else "",
            groups=[],
        )

    def _graph_error(self, *, status_code: int, operation: str) -> AuthenticationError:
        """
        Map a Microsoft Graph failure to an authentication error.

        Returns:
            AuthenticationError: The mapped authentication or service availability error.
        """
        logger.warning("Microsoft Graph %s returned status %d", operation, status_code)
        if status_code == 401:
            return AuthenticationError(status_code=401, detail="Invalid or expired token")
        if status_code == 403:
            return AuthenticationError(
                status_code=403,
                detail="Required Microsoft Graph permission has not been granted",
            )
        return self._graph_unavailable_error()

    def _graph_unavailable_error(self) -> AuthenticationError:
        """
        Build an error for an unavailable or malformed Graph service.

        Returns:
            AuthenticationError: A service unavailable error.
        """
        return AuthenticationError(
            status_code=503,
            detail="Microsoft Graph is temporarily unavailable",
        )
