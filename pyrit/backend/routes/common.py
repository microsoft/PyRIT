# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared route helpers."""

from pyrit.backend.models.common import validate_label_filter


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
