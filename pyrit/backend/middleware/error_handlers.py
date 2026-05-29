# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Error handling middleware for RFC 7807 compliant responses.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from pyrit.backend.models.common import FieldError, ProblemDetail

logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    """Register all error handlers with the FastAPI app."""

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """
        Handle Pydantic validation errors with RFC 7807 format.

        Returns:
            JSONResponse: RFC 7807 problem detail response with validation errors.
        """
        errors = []
        for error in exc.errors():
            field_path = ".".join(str(loc) for loc in error["loc"])
            errors.append(
                FieldError(
                    field=field_path,
                    message=error["msg"],
                    code=error["type"],
                )
            )

        problem = ProblemDetail(
            type="/errors/validation-error",
            title="Validation Error",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Request validation failed",
            instance=str(request.url.path),
            errors=errors,
        )

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=problem.model_dump(exclude_none=True),
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(
        request: Request,
        exc: ValueError,
    ) -> JSONResponse:
        """
        Handle ValueError as 400 Bad Request.

        Returns:
            JSONResponse: RFC 7807 problem detail response with 400 status.
        """
        problem = ProblemDetail(
            type="/errors/bad-request",
            title="Bad Request",
            status=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
            instance=str(request.url.path),
        )

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=problem.model_dump(exclude_none=True),
        )

    @app.exception_handler(FileNotFoundError)
    async def not_found_handler(
        request: Request,
        exc: FileNotFoundError,
    ) -> JSONResponse:
        """
        Handle FileNotFoundError as 404 Not Found.

        Returns:
            JSONResponse: RFC 7807 problem detail response with 404 status.
        """
        problem = ProblemDetail(
            type="/errors/not-found",
            title="Not Found",
            status=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
            instance=str(request.url.path),
        )

        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=problem.model_dump(exclude_none=True),
        )

    @app.exception_handler(PermissionError)
    async def permission_error_handler(
        request: Request,
        exc: PermissionError,
    ) -> JSONResponse:
        """
        Handle PermissionError as 403 Forbidden.

        Returns:
            JSONResponse: RFC 7807 problem detail response with 403 status.
        """
        problem = ProblemDetail(
            type="/errors/forbidden",
            title="Forbidden",
            status=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
            instance=str(request.url.path),
        )

        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=problem.model_dump(exclude_none=True),
        )

    @app.exception_handler(NotImplementedError)
    async def not_implemented_handler(
        request: Request,
        exc: NotImplementedError,
    ) -> JSONResponse:
        """
        Handle NotImplementedError as 501 Not Implemented.

        Returns:
            JSONResponse: RFC 7807 problem detail response with 501 status.
        """
        problem = ProblemDetail(
            type="/errors/not-implemented",
            title="Not Implemented",
            status=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc) or "This feature is not yet implemented",
            instance=str(request.url.path),
        )

        return JSONResponse(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            content=problem.model_dump(exclude_none=True),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """
        Handle unexpected exceptions with RFC 7807 format.

        Returns:
            JSONResponse: RFC 7807 problem detail response with 500 status.
        """
        request_id = getattr(request.state, "request_id", None)
        # Log the full exception for debugging
        logger.exception(  # noqa: LOG004 – Starlette exception handler, not a try/except block
            f"Unhandled exception on {request.method} {request.url.path}"
            f"{f' request_id={request_id}' if request_id else ''}: {exc}",
        )

        problem = ProblemDetail(
            type="/errors/internal-error",
            title="Internal Server Error",
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred",
            instance=str(request.url.path),
        )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=problem.model_dump(exclude_none=True),
        )
