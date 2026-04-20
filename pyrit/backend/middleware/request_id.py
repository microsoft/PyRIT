# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Middleware that reads or generates an X-Request-ID header for every request.

The request ID is:
- Read from the incoming ``X-Request-ID`` header if the client provides one
- Generated as a UUID4 if the header is absent
- Stored on ``request.state.request_id`` for use by route handlers
- Included in every response as the ``X-Request-ID`` header
- Logged so backend logs can be correlated with frontend errors
"""

import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a unique request ID to every request/response cycle."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """
        Read or generate a request ID, log it, and attach it to the response.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware / route handler.

        Returns:
            Response: The HTTP response with the X-Request-ID header set.
        """
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        logger.debug(
            "request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
