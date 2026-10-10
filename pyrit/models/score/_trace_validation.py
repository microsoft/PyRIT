# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared validation for inert trace identifiers and exact tool names."""

from typing import Annotated

from pydantic import AfterValidator, Field


def _require_nonzero(value: str) -> str:
    """
    Reject the all-zero identifiers reserved as invalid by W3C Trace Context.

    Returns:
        str: The unchanged valid identifier.

    Raises:
        ValueError: If the identifier is all zeroes.
    """
    if int(value, 16) == 0:
        raise ValueError("Trace and span identifiers must be nonzero.")
    return value


def _require_nonblank(value: str) -> str:
    """
    Reject blank names without changing exact, case-sensitive matching.

    Returns:
        str: The unchanged nonblank name.

    Raises:
        ValueError: If the name contains only whitespace.
    """
    if not value.strip():
        raise ValueError("Tool names must be nonempty and not whitespace-only.")
    return value


TraceId = Annotated[
    str,
    Field(strict=True, min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$"),
    AfterValidator(_require_nonzero),
]
SpanId = Annotated[
    str,
    Field(strict=True, min_length=16, max_length=16, pattern=r"^[0-9a-f]{16}$"),
    AfterValidator(_require_nonzero),
]
ToolName = Annotated[str, Field(strict=True, min_length=1), AfterValidator(_require_nonblank)]
