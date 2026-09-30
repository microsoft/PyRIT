# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Health check endpoints.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/runtime")
async def runtime_readiness_async(request: Request) -> dict[str, str | bool]:
    """
    Expose readiness and generation without administrative details.

    Returns:
        dict[str, str | bool]: Lightweight runtime readiness.
    """
    runtime = getattr(request.app.state, "runtime_lifecycle", None)
    return {
        "ready": runtime is not None and runtime.state == "ready",
        "state": runtime.state if runtime else "failed",
        "generation": runtime.generation if runtime else "",
    }


@router.get("/health")
async def health_check_async() -> dict[str, str]:
    """
    Check the health status of the backend service.

    This endpoint must remain lightweight, auth-free, and database-free.
    The frontend connection health monitor polls it every 60 seconds with a
    5-second timeout to detect backend availability. Adding authentication,
    database queries, or heavy computation here will break that contract.

    Returns:
        dict: Health status information including timestamp.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "service": "pyrit-backend",
    }
