# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The float value a true/false score keeps for ranking, and its metadata key."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pyrit.models.score.score import Score

#: The existing metadata key. Kept as-is for compatibility, so stored scores need no migration.
FLOAT_FEEDBACK_METADATA_KEY = "original_float_value"


class InvalidFloatFeedbackError(ValueError):
    """A score that promised a float value has none, or has one that is not a finite number in ``[0, 1]``."""


@dataclass(frozen=True)
class FloatFeedback:
    """
    The float value behind a true/false verdict, such as the scale score a threshold was applied to.

    This type owns the ``original_float_value`` metadata key and its readers and writers. An
    ordinary read treats missing or unusable data as unset. A strict read is for callers that
    rely on a scorer's promise to keep the value, and rejects missing or invalid data.

    Attributes:
        value (float): A finite number in ``[0, 1]``.
    """

    value: float

    def __post_init__(self) -> None:
        """
        Validate the value.

        Raises:
            InvalidFloatFeedbackError: If the value is not a finite number in ``[0, 1]``.
        """
        error = _describe_invalid(self.value)
        if error is not None:
            raise InvalidFloatFeedbackError(f"Float feedback {error}.")

    @classmethod
    def read(cls, score: Score | None) -> FloatFeedback | None:
        """
        Read the float value a score kept, treating missing or unusable data as unset.

        Args:
            score (Score | None): The score to read.

        Returns:
            FloatFeedback | None: The value, or None when the score has no usable one.
        """
        raw = cls._raw(score)
        if raw is None or _describe_invalid(raw) is not None:
            return None
        return cls(value=float(raw))

    @classmethod
    def require(cls, score: Score) -> FloatFeedback:
        """
        Read the float value a score promised to keep.

        Args:
            score (Score): A complete true/false score from a scorer that guarantees the value.

        Returns:
            FloatFeedback: The value.

        Raises:
            InvalidFloatFeedbackError: If the value is missing or is not a finite number in ``[0, 1]``.
        """
        raw = cls._raw(score)
        scorer = score.scorer_class_identifier.class_name if score.scorer_class_identifier else "the scorer"
        if raw is None:
            raise InvalidFloatFeedbackError(
                f"{scorer} promised a float value on each true/false result, but this result has none."
            )
        error = _describe_invalid(raw)
        if error is not None:
            raise InvalidFloatFeedbackError(f"{scorer} promised a float value, but the stored one {error}.")
        return cls(value=float(raw))

    def write_to(self, metadata: Mapping[str, str | int | float] | None) -> dict[str, str | int | float]:
        """
        Return a copy of score metadata that carries this value.

        Args:
            metadata (Mapping[str, str | int | float] | None): The metadata to copy.

        Returns:
            dict[str, str | int | float]: The copy, with the value under the feedback key.
        """
        return {**(metadata or {}), FLOAT_FEEDBACK_METADATA_KEY: self.value}

    @staticmethod
    def remove_from(metadata: Mapping[str, str | int | float] | None) -> dict[str, str | int | float]:
        """
        Return a copy of score metadata without a float value.

        Use this when a result no longer judges the same criterion as the value it carried,
        for example after inverting a verdict.

        Args:
            metadata (Mapping[str, str | int | float] | None): The metadata to copy.

        Returns:
            dict[str, str | int | float]: The copy, without the feedback key.
        """
        return {key: value for key, value in (metadata or {}).items() if key != FLOAT_FEEDBACK_METADATA_KEY}

    @staticmethod
    def _raw(score: Score | None) -> str | int | float | None:
        if score is None or not score.score_metadata:
            return None
        return score.score_metadata.get(FLOAT_FEEDBACK_METADATA_KEY)


def _describe_invalid(raw: object) -> str | None:
    """
    Explain why a raw value is not usable feedback.

    Returns:
        str | None: The reason, or None when the value is usable.
    """
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return f"is {type(raw).__name__}, not a number"
    if not math.isfinite(raw):
        return f"is {raw}, not a finite number"
    if not 0.0 <= raw <= 1.0:
        return f"is {raw}, outside [0, 1]"
    return None
