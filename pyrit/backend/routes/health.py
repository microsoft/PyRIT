# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Health check endpoints.
"""

from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter()


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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "pyrit-backend",
    }
