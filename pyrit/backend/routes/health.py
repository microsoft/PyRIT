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

    Returns:
        dict: Health status information including timestamp.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "pyrit-backend",
    }
