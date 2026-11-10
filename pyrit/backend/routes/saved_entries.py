# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Saved targets, converters, scorers, and techniques, addressed by their stored files."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from pyrit.backend.middleware.auth import current_user_oid, get_authentication_policy, has_admin_access
from pyrit.backend.models import ProblemDetail
from pyrit.backend.models.saved_entries import SavedEntryResponse
from pyrit.backend.services.saved_entries import SavedEntryStorageError
from pyrit.backend.services.saved_entry_service import (
    SavedEntryCaller,
    SavedEntryForbiddenError,
    SavedEntryInUseError,
    SavedEntryNotFoundError,
    SavedEntryService,
    SavedEntryVersionRequiredError,
)
from pyrit.models import REGISTRY_NAME_PATTERN
from pyrit.models.catalog import RegistryRecipeKind

router = APIRouter(prefix="/saved-entries", tags=["saved-entries"])

SavedEntryName = Annotated[str, Path(pattern=REGISTRY_NAME_PATTERN, description="The saved entry's name")]


def _caller(request: Request) -> SavedEntryCaller:
    """
    Work out who is asking.

    Returns:
        SavedEntryCaller: The caller's object ID, whether they're an administrator, and whether sign-in is on.
    """
    return SavedEntryCaller(
        oid=current_user_oid(request),
        is_admin=has_admin_access(request),
        sign_in=get_authentication_policy(request).mode != "local",
    )


def _recipes_source(request: Request) -> str | None:
    source = getattr(request.app.state, "registry_recipes_source", None)
    return source if isinstance(source, str) else None


@router.get(
    "/{kind}/{name}",
    response_model=SavedEntryResponse,
    responses={
        404: {"model": ProblemDetail, "description": "No saved entry of the kind has the name"},
        503: {"model": ProblemDetail, "description": "Saved entries can't be read right now"},
    },
)
async def get_saved_entry(  # pyrit-async-suffix-exempt
    kind: RegistryRecipeKind, name: SavedEntryName, request: Request
) -> SavedEntryResponse:
    """
    Get a saved entry's file version, who saved it, and whether the caller may delete it.

    Returns:
        SavedEntryResponse: The file's description.

    Raises:
        HTTPException: 404 if no saved entry of the kind has the name, 503 if storage can't be read.
    """
    try:
        return await SavedEntryService().get_saved_entry_async(
            kind=kind, name=name, recipes_source=_recipes_source(request), caller=_caller(request)
        )
    except SavedEntryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except SavedEntryStorageError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e


@router.delete(
    "/{kind}/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        403: {"model": ProblemDetail, "description": "Only the person who saved it or an administrator can delete it"},
        404: {"model": ProblemDetail, "description": "No saved entry of the kind has the name"},
        409: {
            "model": ProblemDetail,
            "description": "It changed since it was read, something uses it, or work that could use it is in progress",
        },
        422: {"model": ProblemDetail, "description": "expected_version is missing"},
        503: {"model": ProblemDetail, "description": "Saved entries can't be read or written right now"},
    },
)
async def delete_saved_entry(  # pyrit-async-suffix-exempt
    kind: RegistryRecipeKind,
    name: SavedEntryName,
    request: Request,
    expected_version: Annotated[
        str | None,
        Query(
            max_length=128,
            description="The version GET returned, so a file that changed since then isn't deleted. "
            "Only a file that can't be read is deleted without one.",
        ),
    ] = None,
) -> None:
    """
    Delete a saved entry's file and remove the entry it restored, so it doesn't come back after a restart.

    Raises:
        HTTPException: 403, 404, 409, 422, or 503, with the reason.
    """
    try:
        await SavedEntryService().delete_saved_entry_async(
            kind=kind,
            name=name,
            expected_version=expected_version,
            recipes_source=_recipes_source(request),
            caller=_caller(request),
            runtime=getattr(request.app.state, "runtime_lifecycle", None),
        )
    except SavedEntryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except SavedEntryForbiddenError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except SavedEntryVersionRequiredError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    except SavedEntryInUseError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except SavedEntryStorageError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
