# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared route helpers."""

from fastapi import Request, status
from fastapi.responses import JSONResponse

from pyrit.backend.middleware.auth import AuthenticatedUser
from pyrit.backend.models.common import ProblemDetail, validate_label_filter
from pyrit.models.catalog.registry_recipe import SavedEntryOwner

# Problem type for an entry that can't be saved as asked but can still be created without saving it.
NOT_SAVEABLE_PROBLEM_TYPE = "/errors/not-saveable"


def parse_label_query_params(label_params: list[str] | None) -> dict[str, list[str]] | None:
    """
    Parse repeated ``key:value`` label query parameters.

    Returns:
        dict[str, list[str]] | None: Labels grouped with OR-within-key semantics.

    Raises:
        ValueError: If a label filter has no ``:`` separator or a part is too long.
    """
    labels: dict[str, list[str]] = {}
    for param in label_params or []:
        key, _, value = validate_label_filter(param).partition(":")
        labels.setdefault(key.strip(), []).append(value.strip())
    return labels or None


def saved_entry_owner_from_request(request: Request) -> SavedEntryOwner | None:
    """
    Return the signed-in user to record on a saved entry.

    Args:
        request: The incoming FastAPI request.

    Returns:
        SavedEntryOwner | None: The user's object ID and name, or None when sign-in is off.
    """
    user = getattr(request.state, "user", None)
    if not isinstance(user, AuthenticatedUser):
        return None
    return SavedEntryOwner(oid=user.oid, name=user.name or user.email)


def not_saveable_response(*, detail: str, request: Request) -> JSONResponse:
    """
    Build the problem response for an entry that can't be saved as asked.

    Args:
        detail: Why the entry can't be saved.
        request: The incoming FastAPI request.

    Returns:
        JSONResponse: A 400 problem response of type ``NOT_SAVEABLE_PROBLEM_TYPE``.
    """
    problem = ProblemDetail(
        type=NOT_SAVEABLE_PROBLEM_TYPE,
        title="Can't be saved",
        status=status.HTTP_400_BAD_REQUEST,
        detail=detail,
        instance=str(request.url.path),
    )
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=problem.model_dump(exclude_none=True))
