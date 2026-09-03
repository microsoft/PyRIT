# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pyrit.models.score.condition import Condition, condition_from_dict, condition_to_dict


@dataclass(frozen=True, kw_only=True)
class ScoringExpectation:
    """
    What a scorer scores against.

    An expectation is a single parameter, so a question authored in a technique
    configuration or a seed can reach a scorer through an attack that knows nothing
    about it. It has two independent axes.

    ``objective`` carries the intent: prose describing what the run is trying to do.
    Components read it for framing — an adversarial target renders it into a system
    prompt, a report prints it — and none of them match it.

    ``conditions`` carry the criteria: typed objects routed by type to the scorers that
    match them. Attacks forward them without inspecting them, and a scorer matches at
    most one of them.
    """

    #: Version of the persisted shape. Bumped only when the serialized dict changes in a way
    #: an older reader cannot understand; ``scoring_expectation_from_dict`` rejects other values.
    SCHEMA_VERSION: ClassVar[int] = 1

    objective: str | None = None
    conditions: tuple[Condition, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """
        Validate the two axes at construction time.

        Raises:
            TypeError: If ``objective`` is not ``str | None`` or a condition is not a
                ``Condition``.
        """
        if self.objective is not None and not isinstance(self.objective, str):
            raise TypeError(
                f"ScoringExpectation objective must be a string or None, got {type(self.objective).__name__}."
            )
        for condition in self.conditions:
            if not isinstance(condition, Condition):
                raise TypeError(
                    f"ScoringExpectation conditions must all be Condition instances, got {type(condition).__name__}."
                )


def scoring_expectation_to_dict(exp: ScoringExpectation) -> dict[str, Any]:
    """
    Serialize an expectation to a versioned, JSON-native dict.

    Args:
        exp (ScoringExpectation): The expectation to serialize.

    Returns:
        dict[str, Any]: ``{'schema_version': …, 'objective': …, 'conditions': [ … ]}``.
    """
    return {
        "schema_version": ScoringExpectation.SCHEMA_VERSION,
        "objective": exp.objective,
        "conditions": [condition_to_dict(condition) for condition in exp.conditions],
    }


def scoring_expectation_from_dict(value: dict[str, Any]) -> ScoringExpectation:
    """
    Rebuild an expectation from a versioned dict produced by ``scoring_expectation_to_dict``.

    Args:
        value (dict[str, Any]): The serialized expectation.

    Returns:
        ScoringExpectation: The reconstructed expectation.

    Raises:
        ValueError: If the schema version is unsupported, an unknown top-level field is
            present, ``objective`` is not ``str | None``, or ``conditions`` is not a list of
            dicts.
    """
    unknown = set(value) - {"schema_version", "objective", "conditions"}
    if unknown:
        raise ValueError(f"Unknown ScoringExpectation field(s): {sorted(unknown)}.")

    schema_version = value.get("schema_version")
    if schema_version != ScoringExpectation.SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported ScoringExpectation schema_version {schema_version!r}; "
            f"expected {ScoringExpectation.SCHEMA_VERSION}."
        )

    objective = value.get("objective")
    if objective is not None and not isinstance(objective, str):
        raise ValueError(f"ScoringExpectation objective must be a string or None, got {type(objective).__name__}.")

    raw_conditions = value.get("conditions", [])
    if not isinstance(raw_conditions, list) or not all(isinstance(item, dict) for item in raw_conditions):
        raise ValueError("ScoringExpectation conditions must be a list of dicts.")

    conditions = tuple(condition_from_dict(item) for item in raw_conditions)
    return ScoringExpectation(objective=objective, conditions=conditions)


def scoring_expectation_fingerprint(exp: ScoringExpectation) -> str:
    """
    Return a stable content fingerprint of an expectation.

    The fingerprint is the lowercase SHA-256 hex of the canonical JSON serialization
    (sorted keys, compact separators), so two expectations with the same objective and
    conditions hash identically regardless of construction order.

    Args:
        exp (ScoringExpectation): The expectation to fingerprint.

    Returns:
        str: The lowercase SHA-256 hex digest.
    """
    serialized = json.dumps(
        scoring_expectation_to_dict(exp),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
