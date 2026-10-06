# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared route helpers."""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, status

from pyrit.backend.models.common import ProblemDetail, validate_label_filter
from pyrit.backend.services.instance_persistence_service import (
    AdministratorRequiredError,
    InstanceConflictError,
    InstanceNotFoundError,
    InstanceStoreUnavailableError,
    PreconditionRequiredError,
)

SAVED_INSTANCE_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_400_BAD_REQUEST: {
        "model": ProblemDetail,
        "description": "Invalid type, parameters, or credential references",
    },
    status.HTTP_403_FORBIDDEN: {
        "model": ProblemDetail,
        "description": "Credential references require administrator access",
    },
    status.HTTP_409_CONFLICT: {
        "model": ProblemDetail,
        "description": "Name in use, stale version, or referenced by saved instances",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ProblemDetail, "description": "Saved instance store unavailable"},
}


@contextmanager
def translate_saved_instance_errors(*, action: str) -> Iterator[None]:
    """
    Map errors from saving, replacing, or deleting an instance to HTTP responses.

    Args:
        action (str): What failed, for unexpected errors (for example ``"create target"``).

    Raises:
        HTTPException: 403 without administrator access, 404 for an unknown saved instance,
            428 without an expected version, 409 for a conflict, 503 when the store is
            unavailable, 400 for an invalid request, and 500 otherwise.
    """
    try:
        yield
    except HTTPException:
        raise
    except AdministratorRequiredError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except InstanceNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except PreconditionRequiredError as error:
        raise HTTPException(status_code=status.HTTP_428_PRECONDITION_REQUIRED, detail=str(error)) from error
    except InstanceConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except InstanceStoreUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to {action}: {error}"
        ) from error


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
