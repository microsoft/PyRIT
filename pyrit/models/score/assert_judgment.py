# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The ASSERT judgment data that ASSERT judges record in score metadata."""

from __future__ import annotations

import json
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

if TYPE_CHECKING:
    from collections.abc import Mapping

_Grade = StrictInt | StrictStr
_ScoreMetadata = dict[str, str | int | float]


class _AssertScoreMetadata(BaseModel):
    """A value object that alone reads and writes its ``KEYS`` in score metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    KEYS: ClassVar[tuple[str, ...]]

    @classmethod
    def from_metadata(cls, *, metadata: Mapping[str, Any] | None) -> Self | None:
        """
        Read this object's keys from score metadata.

        Args:
            metadata (Mapping[str, Any] | None): The score metadata.

        Returns:
            Self | None: The recorded data, or None when none of its keys are present.

        Raises:
            ValueError: If a present key is malformed.
        """
        present = {key: value for key, value in (metadata or {}).items() if key in cls.KEYS}
        return cls._decode(present) if present else None

    @classmethod
    def _decode(cls, values: dict[str, Any]) -> Self:
        raise NotImplementedError

    def to_metadata(self) -> _ScoreMetadata:
        """
        Return the score metadata keys for this data.

        Returns:
            dict[str, str | int | float]: The keys that are set.
        """
        raise NotImplementedError

    def merge_into(self, metadata: Mapping[str, str | int | float] | None) -> _ScoreMetadata:
        """
        Replace this object's keys in score metadata and keep every other key.

        Args:
            metadata (Mapping[str, str | int | float] | None): The score metadata.

        Returns:
            dict[str, str | int | float]: A new metadata dict.
        """
        merged: _ScoreMetadata = {key: value for key, value in (metadata or {}).items() if key not in self.KEYS}
        merged.update(self.to_metadata())
        return merged


def _load_json(values: dict[str, Any], *, key: str) -> Any:
    raw = values[key]
    if not isinstance(raw, str):
        raise ValueError(f"Score metadata '{key}' must be a JSON string.")
    return json.loads(raw)


def _load_flag(values: dict[str, Any], *, key: str) -> bool:
    raw = values[key]
    if raw not in ("true", "false"):
        raise ValueError(f"Score metadata '{key}' must be 'true' or 'false'.")
    return bool(raw == "true")


def _dump_flag(value: bool) -> str:
    return "true" if value else "false"


def _require(values: dict[str, Any], *keys: str) -> None:
    missing = [key for key in keys if key not in values]
    if missing:
        raise ValueError(f"Score metadata is missing {', '.join(missing)}.")


class AssertSkipReason(str, Enum):
    """Why an ASSERT scenario didn't judge a conversation."""

    TARGET_INPUT_REFUSED = "target_input_refused"
    TARGET_ERROR = "target_error"


class AssertDimensionResult(_AssertScoreMetadata):
    """
    Which ASSERT dimension a score judges, and what it records beyond the true/false verdict.

    A not-applicable answer and a skipped conversation are undetermined scores. A graded dimension
    records its declared grades in order, and the grade the judge chose.
    """

    KEYS: ClassVar[tuple[str, ...]] = (
        "assert_dimension",
        "assert_scoring_skipped",
        "assert_not_applicable",
        "assert_grade",
        "assert_scale",
    )

    dimension: Annotated[str, Field(min_length=1)] | None = None
    skip_reason: AssertSkipReason | None = None
    not_applicable: StrictBool = False
    grade: _Grade | None = None
    scale: tuple[StrictInt, ...] | tuple[StrictStr, ...] | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.scale is not None and (len(self.scale) < 2 or len(set(self.scale)) != len(self.scale)):
            raise ValueError("A scale must list at least two distinct grades.")
        if self.grade is not None and (self.scale is None or self.grade not in self.scale):
            raise ValueError(f"Grade {self.grade!r} is not in the recorded scale.")
        if self.grade is not None and self.not_applicable:
            raise ValueError("A not-applicable result has no grade.")
        if self.skip_reason is not None and (self.not_applicable or self.grade is not None):
            raise ValueError("A skipped conversation has no answer.")
        return self

    @classmethod
    def _decode(cls, values: dict[str, Any]) -> Self:
        scale = _load_json(values, key="assert_scale") if "assert_scale" in values else None
        return cls.model_validate(
            {
                "dimension": values.get("assert_dimension"),
                "skip_reason": values.get("assert_scoring_skipped"),
                "not_applicable": _load_flag(values, key="assert_not_applicable")
                if "assert_not_applicable" in values
                else False,
                "grade": _load_json(values, key="assert_grade") if "assert_grade" in values else None,
                "scale": tuple(scale) if isinstance(scale, list) else scale,
            }
        )

    def to_metadata(self) -> _ScoreMetadata:
        """
        Return the score metadata keys for this data.

        Returns:
            dict[str, str | int | float]: The keys that are set.
        """
        metadata: _ScoreMetadata = {}
        if self.dimension is not None:
            metadata["assert_dimension"] = self.dimension
        if self.skip_reason is not None:
            metadata["assert_scoring_skipped"] = self.skip_reason.value
        if self.not_applicable:
            metadata["assert_not_applicable"] = _dump_flag(True)
        if self.grade is not None:
            metadata["assert_grade"] = json.dumps(self.grade)
        if self.scale is not None:
            metadata["assert_scale"] = json.dumps(list(self.scale))
        return metadata


class AssertNodeJudgment(BaseModel):
    """The judge's verdict for one taxonomy category it found relevant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_name: StrictStr
    violated: StrictBool
    confidence: Literal["high", "medium", "low"]
    reasoning: StrictStr


class AssertCategoryJudgment(_AssertScoreMetadata):
    """
    The per-category judgments of a category-validated ASSERT judge.

    Only relevant categories are listed. ``original_score_value`` is the judge's own verdict, before
    ``policy_violation`` is recomputed from the categories.
    """

    KEYS: ClassVar[tuple[str, ...]] = (
        "assert_judgment_mode",
        "assert_node_judgments",
        "assert_original_score_value",
        "assert_correction_reason",
        "assert_original_description",
        "assert_judgment_warnings",
    )
    _MODE: ClassVar[str] = "category_validated"

    node_judgments: tuple[AssertNodeJudgment, ...]
    original_score_value: StrictBool
    correction_reason: StrictStr | None = None
    original_description: StrictStr | None = None
    warnings: tuple[StrictStr, ...] = ()

    @model_validator(mode="after")
    def _check_correction(self) -> Self:
        if (self.correction_reason is None) != (self.original_description is None):
            raise ValueError("A correction records both its reason and the original description.")
        return self

    @classmethod
    def _decode(cls, values: dict[str, Any]) -> Self:
        _require(values, "assert_judgment_mode", "assert_node_judgments", "assert_original_score_value")
        if values["assert_judgment_mode"] != cls._MODE:
            raise ValueError(f"Score metadata 'assert_judgment_mode' must be '{cls._MODE}'.")
        nodes = _load_json(values, key="assert_node_judgments")
        warnings = _load_json(values, key="assert_judgment_warnings") if "assert_judgment_warnings" in values else []
        if not isinstance(nodes, list) or not isinstance(warnings, list):
            raise ValueError("ASSERT node judgments and warnings must be JSON lists.")
        return cls.model_validate(
            {
                "node_judgments": nodes,
                "original_score_value": _load_flag(values, key="assert_original_score_value"),
                "correction_reason": values.get("assert_correction_reason"),
                "original_description": values.get("assert_original_description"),
                "warnings": warnings,
            }
        )

    def to_metadata(self) -> _ScoreMetadata:
        """
        Return the score metadata keys for this data.

        Returns:
            dict[str, str | int | float]: The keys that are set.
        """
        nodes = [node.model_dump(mode="json") for node in self.node_judgments]
        metadata: _ScoreMetadata = {
            "assert_judgment_mode": self._MODE,
            "assert_node_judgments": json.dumps(nodes, ensure_ascii=True),
            "assert_original_score_value": _dump_flag(self.original_score_value),
        }
        if self.correction_reason is not None and self.original_description is not None:
            metadata["assert_correction_reason"] = self.correction_reason
            metadata["assert_original_description"] = self.original_description
        if self.warnings:
            metadata["assert_judgment_warnings"] = json.dumps(list(self.warnings))
        return metadata


class AssertSampleVote(str, Enum):
    """One judge sample's answer in a repeated ASSERT judgment."""

    TRUE = "true"
    FALSE = "false"
    UNDETERMINED = "undetermined"
    NOT_APPLICABLE = "not_applicable"


class AssertSampleVotes(_AssertScoreMetadata):
    """
    Every sample's answer when an ASSERT judge is repeated, and how much the decided samples agree.

    ``voted_score_value`` is the plain majority, recorded only when the verdict was recomputed from
    the voted category judgments and differs from it.
    """

    KEYS: ClassVar[tuple[str, ...]] = (
        "assert_sample_count",
        "assert_sample_votes",
        "assert_sample_agreement",
        "assert_voted_score_value",
    )

    votes: Annotated[tuple[AssertSampleVote, ...], Field(min_length=2)]
    agreement: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    voted_score_value: StrictBool | None = None

    @property
    def count(self) -> int:
        """The number of samples."""
        return len(self.votes)

    @classmethod
    def _decode(cls, values: dict[str, Any]) -> Self:
        _require(values, "assert_sample_count", "assert_sample_votes")
        votes = _load_json(values, key="assert_sample_votes")
        count = values["assert_sample_count"]
        if not isinstance(votes, list) or isinstance(count, bool) or count != len(votes):
            raise ValueError("Score metadata 'assert_sample_count' must match 'assert_sample_votes'.")
        agreement = values.get("assert_sample_agreement")
        if isinstance(agreement, bool) or (agreement is not None and not isinstance(agreement, (int, float))):
            raise ValueError("Score metadata 'assert_sample_agreement' must be a number.")
        return cls.model_validate(
            {
                "votes": votes,
                "agreement": agreement,
                "voted_score_value": _load_flag(values, key="assert_voted_score_value")
                if "assert_voted_score_value" in values
                else None,
            }
        )

    def to_metadata(self) -> _ScoreMetadata:
        """
        Return the score metadata keys for this data.

        Returns:
            dict[str, str | int | float]: The keys that are set.
        """
        metadata: _ScoreMetadata = {
            "assert_sample_count": self.count,
            "assert_sample_votes": json.dumps([vote.value for vote in self.votes]),
        }
        if self.agreement is not None:
            metadata["assert_sample_agreement"] = self.agreement
        if self.voted_score_value is not None:
            metadata["assert_voted_score_value"] = _dump_flag(self.voted_score_value)
        return metadata
