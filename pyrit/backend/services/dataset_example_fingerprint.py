# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Selection-key parsing and the dataset-example content fingerprint."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from pyrit.models import SeedRecord

_NAMED_PREFIX = "dataset:named:"
_UNNAMED_KEY = "dataset:unnamed"


def parse_dataset_selection_key(selection_key: str) -> str | None:
    """
    Return the dataset name for a browse selection key.

    Returns:
        str | None: The dataset name, or None for the unnamed population.

    Raises:
        ValueError: If the key is not ``dataset:unnamed`` or ``dataset:named:<non-empty>``.
    """
    if selection_key == _UNNAMED_KEY:
        return None
    name = selection_key.removeprefix(_NAMED_PREFIX)
    if not name or name == selection_key:
        raise ValueError(f"Invalid dataset selection key: {selection_key}")
    return name


def dataset_example_fingerprint(*, selection_key: str, example_id: UUID, members: Sequence[SeedRecord]) -> str:
    """
    Hash one logical example's browse identity and stored inspection records.

    Member order is the memory order. The digest changes when members are added or removed
    and when any stored ``SeedRecord`` field changes, including values, roles, sequence,
    media references, conditions, and provenance. ``value_url`` is not part of ``SeedRecord``
    and is not hashed.

    Returns:
        str: Lowercase SHA-256 hex digest, 64 characters.
    """
    document: dict[str, Any] = {
        "v": 1,
        "selection_key": selection_key,
        "example_id": str(example_id).lower(),
        "members": [member.model_dump(mode="json") for member in members],
    }
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
