# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Reject incompatible API clients before authentication or application work."""

from typing import ClassVar

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from pyrit import _compatibility


class CompatibilityMiddleware:
    """Enforce one exact packaged compatibility identity on business API requests."""

    _NEUTRAL_PATHS: ClassVar[frozenset[str]] = frozenset(
        {"/api/health", "/api/auth/config", "/api/version", "/api/media"}
    )

    def __init__(self, app: ASGIApp) -> None:
        """Resolve packaged provenance when the application middleware is built."""
        self.app = app
        self._compatibility_id = _compatibility.get_compatibility_id()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Validate the marker without consuming the body or invoking dependencies."""
        compatibility_id = self._compatibility_id
        if scope["type"] == "http" and "app" in scope:
            state = scope["app"].state
            if not hasattr(state, "compatibility_id"):
                state.compatibility_id = compatibility_id
            compatibility_id = state.compatibility_id

        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope["method"] == "OPTIONS"
            or (path != "/api" and not path.startswith("/api/"))
            or path in self._NEUTRAL_PATHS
        ):
            await self.app(scope, receive, send)
            return

        markers = Headers(scope=scope).getlist(_compatibility.COMPATIBILITY_HEADER)
        actual = ", ".join(markers) if markers else None
        if len(markers) != 1 or not _compatibility.is_valid_compatibility_id(actual):
            status = 400
            problem_type = _compatibility.INVALID_COMPATIBILITY_TYPE
            title = "Invalid compatibility ID"
            detail = f"Supply exactly one valid {_compatibility.COMPATIBILITY_HEADER} header."
        elif actual != compatibility_id:
            status = 409
            problem_type = _compatibility.MISMATCH_COMPATIBILITY_TYPE
            title = "Compatibility ID mismatch"
            detail = "The client and backend must use the same PyRIT build."
        else:
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            status_code=status,
            media_type="application/problem+json",
            content={
                "type": problem_type,
                "title": title,
                "status": status,
                "detail": detail,
                "expected": compatibility_id,
                "actual": actual,
            },
        )
        await response(scope, receive, send)
