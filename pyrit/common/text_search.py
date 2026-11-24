# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Portable text-search syntax shared by lightweight filter models and memory."""

from collections.abc import Sequence


def contains_pattern(value: str) -> str:
    """
    Encode a literal substring for LIKE with '/' escaping, including SQL Server brackets.

    Returns:
        str: An escaped pattern surrounded by '%' wildcards.
    """
    escaped = "".join("/" + char if char in "/%_[" else char for char in value)
    return f"%{escaped}%"


def compile_like_pattern(pattern: str) -> str:
    """
    Encode a full portable LIKE pattern: only unescaped '%' and '_' are wildcards.

    '/' escapes the next character; brackets, '*' and backslashes are literal.

    Returns:
        str: A LIKE pattern with portable escaping and unchanged wildcard positions.

    Raises:
        ValueError: If the pattern ends with an unpaired '/' escape.
    """
    result: list[str] = []
    escaped = False
    for char in pattern:
        if not escaped and char == "/":
            escaped = True
            continue
        result.append("/" + char if char in "/[" or (escaped and char in "%_") else char)
        escaped = False
    if escaped:
        raise ValueError("A text search pattern cannot end with an unpaired '/' escape.")
    return "".join(result)


def validate_text_search(*, value: str | Sequence[str] | None, pattern: str | Sequence[str] | None) -> None:
    """
    Reject conflicting literal/pattern filters and invalid pattern escapes.

    Raises:
        ValueError: If both filters are supplied or a pattern has a dangling escape.
    """
    if value is not None and pattern is not None:
        raise ValueError("Supply either a literal text filter or its pattern companion, not both.")
    if pattern is not None:
        for item in (pattern,) if isinstance(pattern, str) else pattern:
            compile_like_pattern(item)
