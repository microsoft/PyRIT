# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Reject incompatible API clients before authentication or application work."""

from typing import Any, ClassVar

from fastapi import FastAPI
from starlette._utils import get_route_path
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
        """Wrap the application without loading provenance before lifespan startup."""
        self.app = app

    @classmethod
    def requires_compatibility(cls, *, path: str, method: str) -> bool:
        """Return whether an application-relative path and method require the marker."""
        return method != "OPTIONS" and (path == "/api" or path.startswith("/api/")) and path not in cls._NEUTRAL_PATHS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Validate the marker without consuming the body or invoking dependencies."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.requires_compatibility(path=get_route_path(scope), method=scope["method"]):
            await self.app(scope, receive, send)
            return

        compatibility_id = scope["app"].state.compatibility_id
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


class CompatibilityAPI(FastAPI):
    """Document the middleware's required header without adding runtime dependencies."""

    def openapi(self) -> dict[str, Any]:
        """
        Extend the framework-generated schema while preserving its generation and caching.

        Returns:
            The OpenAPI schema with the required business-request header documented.
        """
        schema = super().openapi()
        for path, path_item in schema.get("paths", {}).items():
            for method, operation in path_item.items():
                if method not in {"get", "post", "put", "patch", "delete", "head", "trace"}:
                    continue
                if not CompatibilityMiddleware.requires_compatibility(path=path, method=method.upper()):
                    continue
                parameters = operation.setdefault("parameters", [])
                if not any(
                    parameter.get("in") == "header"
                    and parameter.get("name", "").lower() == _compatibility.COMPATIBILITY_HEADER.lower()
                    for parameter in parameters
                ):
                    parameters.append(
                        {
                            "name": _compatibility.COMPATIBILITY_HEADER,
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": (
                                "The caller's packaged <Python package version>+g<full source commit> identity. "
                                "Authenticate and compare it with /api/version before sending business requests. "
                                "Do not copy the backend's identity."
                            ),
                        }
                    )
        return schema
