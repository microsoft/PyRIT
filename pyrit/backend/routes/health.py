# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Health check endpoints.
"""

import os
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response

router = APIRouter()


def _runtime_readiness(request: Request) -> dict[str, str | bool]:
    runtime = getattr(request.app.state, "runtime_lifecycle", None)
    if runtime is None:
        return {"ready": False, "state": "failed", "generation": ""}
    return {
        "ready": runtime.reported_state == "ready",
        "state": runtime.reported_state,
        "generation": runtime.generation,
    }


@router.get("/runtime")
async def runtime_readiness_async(request: Request) -> dict[str, str | bool]:
    """
    Expose readiness and generation without administrative details.

    Returns:
        dict[str, str | bool]: Lightweight runtime readiness.
    """
    return _runtime_readiness(request)


@router.get("/ready", responses={503: {"description": "PyRIT runtime is not ready"}})
async def deployment_readiness_async(*, request: Request, response: Response) -> dict[str, str | bool | None]:
    """
    Report uncached deployment readiness without configuration or exception details.

    Returns:
        dict[str, str | bool | None]: Runtime readiness and the serving ACA revision, when available.
    """
    readiness = _runtime_readiness(request)
    response.status_code = 200 if readiness["ready"] else 503
    response.headers["Cache-Control"] = "no-store"
    return {
        "ready": readiness["ready"],
        "state": readiness["state"],
        "revision": os.getenv("CONTAINER_APP_REVISION"),
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
