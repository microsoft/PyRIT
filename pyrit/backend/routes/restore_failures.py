# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Saved registry entries that couldn't be restored, listed for every signed-in user."""

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

from pyrit.backend.middleware.auth import has_admin_access

if TYPE_CHECKING:
    from pyrit.models.catalog import RestoreFailure

router = APIRouter(prefix="/restore-failures", tags=["restore-failures"])


@router.get("")
async def list_restore_failures_async(request: Request) -> dict[str, list[dict[str, Any]]]:
    """
    List the saved entries that couldn't be restored and why.

    Administrators get the full message. Other users get a fixed explanation of the reason instead,
    since a constructor's error can quote a server setting.

    Returns:
        dict[str, list[dict[str, Any]]]: The failures, under ``items``.
    """
    runtime = getattr(request.app.state, "runtime_lifecycle", None)
    failures: list[RestoreFailure] = runtime.restore_failures if runtime is not None else []
    show_details = has_admin_access(request)
    items = []
    for failure in failures:
        item = failure.model_dump(mode="json")
        if not show_details:
            item["message"] = failure.reason.explanation
        items.append(item)
    return {"items": items}
