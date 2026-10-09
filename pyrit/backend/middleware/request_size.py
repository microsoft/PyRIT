# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Reject requests whose URL or body is larger than the backend accepts."""

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestTooLargeError(HTTPException):
    """Raised when a request body grows past the size limit while it is read."""

    def __init__(self) -> None:
        """Create the 413 error."""
        super().__init__(status_code=413, detail="The request is larger than the backend accepts.")


def request_too_large_response(*, status: int, title: str) -> JSONResponse:
    """
    Build the RFC 7807 problem response for a request over a size limit.

    Returns:
        JSONResponse: The problem response.
    """
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": "/errors/request-too-large",
            "title": title,
            "status": status,
            "detail": "The request is larger than the backend accepts.",
        },
    )


class RequestSizeLimitMiddleware:
    """Return 414 for oversized URLs and 413 for oversized bodies before route handlers use them."""

    # Large enough for base64-encoded media attachments.
    MAX_BODY_BYTES: int = 100 * 1024 * 1024
    MAX_URL_LENGTH: int = 8 * 1024

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the downstream application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject oversized requests and cap the body bytes the application can read."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("raw_path") or scope["path"].encode()
        if len(path) + len(scope.get("query_string", b"")) > self.MAX_URL_LENGTH:
            await self._reject_async(scope=scope, receive=receive, send=send, status=414, title="URI Too Long")
            return
        content_length = Headers(scope=scope).get("content-length", "")
        if content_length.isdigit() and int(content_length) > self.MAX_BODY_BYTES:
            await self._reject_async(scope=scope, receive=receive, send=send, status=413, title="Content Too Large")
            return
        await self.app(scope, self._limit_body(receive), send)

    def _limit_body(self, receive: Receive) -> Receive:
        """
        Wrap ``receive`` so bodies sent without a reliable ``Content-Length`` are also capped.

        Returns:
            Receive: A receive callable that raises a 413 error once the cap is exceeded.
        """
        received = 0

        async def limited_receive_async() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.MAX_BODY_BYTES:
                    raise RequestTooLargeError
            return message

        return limited_receive_async

    @staticmethod
    async def _reject_async(*, scope: Scope, receive: Receive, send: Send, status: int, title: str) -> None:
        """Send an RFC 7807 problem response without calling the application."""
        await request_too_large_response(status=status, title=title)(scope, receive, send)
