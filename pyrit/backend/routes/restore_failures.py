# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Saved registry entries that couldn't be restored, listed for every signed-in user."""

from typing import Any

from fastapi import APIRouter, Request

from pyrit.backend.middleware.auth import has_admin_access
from pyrit.models.catalog.registry_recipe import RestoreFailure, RestoreFailureReason

router = APIRouter(prefix="/restore-failures", tags=["restore-failures"])

_EXPLANATIONS: dict[RestoreFailureReason, str] = {
    RestoreFailureReason.UNREADABLE_STORAGE: "The folder of saved entries couldn't be read.",
    RestoreFailureReason.UNREADABLE_RECIPE: "The saved file couldn't be read.",
    RestoreFailureReason.INVALID_RECIPE: "The saved file isn't a valid entry.",
    RestoreFailureReason.NEWER_SCHEMA_VERSION: "It was saved by a newer version of PyRIT.",
    RestoreFailureReason.UNKNOWN_CLASS: "Its class or attack type isn't available.",
    RestoreFailureReason.MODULE_MISMATCH: "Its class is now defined somewhere else.",
    RestoreFailureReason.UNSUPPORTED_CLASS: "Its class can't be rebuilt from a saved file.",
    RestoreFailureReason.UNSUPPORTED_CREDENTIAL: "It holds a credential, which a saved file can't hold.",
    RestoreFailureReason.NAME_CLASH: "Its name or one of its tags is already taken.",
    RestoreFailureReason.MISSING_DEPENDENCY: "It uses an entry that doesn't exist.",
    RestoreFailureReason.FAILED_DEPENDENCY: "It uses an entry that couldn't be restored.",
    RestoreFailureReason.DEPENDENCY_LOOP: "It is part of a group of entries that use each other.",
    RestoreFailureReason.BUILD_ERROR: "It couldn't be built.",
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
    show_details = has_admin_access(request)
    items = []
    for failure in failures:
        item = failure.model_dump(mode="json")
        if not show_details:
            item["message"] = _EXPLANATIONS[failure.reason]
        items.append(item)
    return {"items": items}
