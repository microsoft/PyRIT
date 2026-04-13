# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Middleware that adds security-related HTTP response headers.

Applied headers:
- Content-Security-Policy (separate policies for API vs frontend)
- Strict-Transport-Security (production only)
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- Referrer-Policy: strict-origin-when-cross-origin
- Permissions-Policy (disables unused browser APIs)
- Cache-Control: no-store (API routes only)
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Swagger / ReDoc paths — these load scripts and styles from CDN,
# so CSP is skipped in dev mode (production disables these routes entirely).
_DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}

# CSP for API responses — as strict as possible.
_API_CSP = "default-src 'none'; frame-ancestors 'none'"

# CSP for frontend SPA — allows self-hosted scripts and Fluent UI / Griffel
# runtime style injection ('unsafe-inline' for style-src only).
_FRONTEND_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://*.blob.core.windows.net; "
    "media-src 'self' https://*.blob.core.windows.net; "
    "font-src 'self' data:; "
    "connect-src 'self' https://login.microsoftonline.com; "
    "frame-ancestors 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security response headers on every request."""

    def __init__(self, app: ASGIApp, dev_mode: bool = False) -> None:
        """
        Initialize the middleware.

        Args:
            app: The ASGI application.
            dev_mode: When True, HSTS is omitted to avoid breaking local HTTP.
        """
        super().__init__(app)
        self._dev_mode = dev_mode

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """
        Add security headers to the response.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware / route handler.

        Returns:
            Response with security headers applied.
        """
        response = await call_next(request)
        path = request.url.path

        # --- Headers applied to ALL responses ---
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        # HSTS only in production (HTTPS)
        if not self._dev_mode:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # --- Path-dependent headers ---
        if path.startswith("/api"):
            response.headers["Content-Security-Policy"] = _API_CSP
            response.headers["Cache-Control"] = "no-store"
        elif self._dev_mode and path in _DOCS_PATHS:
            pass  # No CSP — Swagger/ReDoc load from CDN
        else:
            response.headers["Content-Security-Policy"] = _FRONTEND_CSP

        return response
