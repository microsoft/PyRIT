# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared route helpers."""


def parse_label_query_params(label_params: list[str] | None) -> dict[str, list[str]] | None:
    """
    Parse repeated ``key:value`` label query parameters.

    Returns:
        dict[str, list[str]] | None: Labels grouped with OR-within-key semantics.
    """
    labels: dict[str, list[str]] = {}
    for param in label_params or []:
        if ":" not in param:
            continue
        key, value = (part.strip() for part in param.split(":", 1))
        labels.setdefault(key, []).append(value)
    return labels or None
