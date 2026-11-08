# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Saved registry entries that couldn't be restored, listed for every signed-in user."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from pyrit.backend.middleware.auth import has_admin_access
from pyrit.models.catalog import RestoreFailure, RestoreFailureReason

router = APIRouter(prefix="/restore-failures", tags=["restore-failures"])

_EXPLANATIONS: dict[RestoreFailureReason, str] = {
    RestoreFailureReason.UNREADABLE_STORAGE: "The folder of saved entries couldn't be read.",
    RestoreFailureReason.UNREADABLE_RECIPE: "The saved file couldn't be read.",
    RestoreFailureReason.INVALID_RECIPE: "The saved file isn't a valid entry.",
    RestoreFailureReason.NEWER_SCHEMA_VERSION: "It was saved by a newer version of PyRIT.",
    RestoreFailureReason.UNKNOWN_CLASS: "Its class or attack type isn't available.",
    RestoreFailureReason.MODULE_MISMATCH: "Its class is now defined somewhere else.",
    RestoreFailureReason.UNSUPPORTED_CLASS: "Its class can't be rebuilt from a saved file.",
    RestoreFailureReason.UNSUPPORTED_CREDENTIAL: (
        "It holds a credential, or would use one of this server's, which a saved entry can't do."
    ),
    RestoreFailureReason.NAME_CLASH: "Its name or one of its tags is already taken.",
    RestoreFailureReason.MISSING_DEPENDENCY: "It uses an entry that doesn't exist.",
    RestoreFailureReason.FAILED_DEPENDENCY: "It uses an entry that couldn't be restored.",
    RestoreFailureReason.DEPENDENCY_LOOP: "It is part of a group of entries that use each other.",
    RestoreFailureReason.BUILD_ERROR: "It couldn't be built.",
}


_REFUSALS: dict[str, str] = {
    "nothing-to-retry": "The runtime isn't waiting on saved entries that couldn't be restored.",
    "unsupported": "Retrying requires one backend worker and one replica. Restart the backend instead.",
}


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
    return {"items": _describe(failures=failures, request=request)}


@router.post("/retry")
async def retry_restore_async(request: Request) -> dict[str, Any]:
    """
    Rebuild the saved entries that weren't restored, without a restart.

    Any signed-in user may retry. Entries that were restored, and those the startup scripts
    created, stay as they are.

    Returns:
        dict[str, Any]: The runtime state after the retry and the failures that remain, under ``items``.

    Raises:
        HTTPException: 409 if no failed restore is waiting, or the backend runs several workers or replicas.
    """
    runtime = request.app.state.runtime_lifecycle
    outcome = await runtime.retry_restore_async()
    if outcome in _REFUSALS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_REFUSALS[outcome])
    return {
        "state": runtime.reported_state,
        "outcome": outcome,
        "items": _describe(failures=runtime.restore_failures, request=request),
    }


def _describe(*, failures: list[RestoreFailure], request: Request) -> list[dict[str, Any]]:
    """
    Describe failures for the caller, hiding error text from anyone who isn't an administrator.

    Returns:
        list[dict[str, Any]]: One JSON-safe item per failure.
    """
    show_details = has_admin_access(request)
    items = []
    for failure in failures:
        item = failure.model_dump(mode="json")
        if not show_details:
            item["message"] = _EXPLANATIONS[failure.reason]
        items.append(item)
    return items
