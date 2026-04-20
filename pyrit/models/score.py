# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Union, get_args

if TYPE_CHECKING:
    from pyrit.identifiers import ScorerIdentifier

ScoreType = Literal["true_false", "float_scale", "unknown"]


class Score:
    id: uuid.UUID | str

    # The value the scorer ended up with; e.g. True (if true_false) or 0 (if float_scale)
    score_value: str

    # Value that can include a description of the score value
    score_value_description: str

    # The type of the scorer; e.g. "true_false" or "float_scale"
    score_type: ScoreType

    # The harms categories (e.g. ["hate", "violence"]) – can be multiple
    score_category: Optional[List[str]]

    # Extra data the scorer provides around the rationale of the score
    score_rationale: str

    # Custom metadata a scorer might use. This can vary by scorer.
    score_metadata: Optional[Dict[str, Union[str, int, float]]]

    # The identifier of the scorer class, including relevant information
    scorer_class_identifier: ScorerIdentifier

    # This is the ID of the MessagePiece that the score is scoring
    # Note a scorer can generate an additional request. This is NOT that, but
    # the ID associated with what we're scoring.
    message_piece_id: uuid.UUID | str

    # Timestamp of when the score was created
    timestamp: datetime

    # The task based on which the text is scored (the original attacker model's objective).
    task: str

    def __init__(
        self,
        *,
        score_value: str,
        score_value_description: str,
        score_type: ScoreType,
        score_rationale: str,
        message_piece_id: str | uuid.UUID,
        id: Optional[uuid.UUID | str] = None,
        score_category: Optional[List[str]] = None,
        score_metadata: Optional[Dict[str, Union[str, int, float]]] = None,
        scorer_class_identifier: Union["ScorerIdentifier", Dict[str, Any]],
        timestamp: Optional[datetime] = None,
        objective: Optional[str] = None,
    ):
        # Import at runtime to avoid circular import
        from pyrit.identifiers import ScorerIdentifier

        self.id = id if id else uuid.uuid4()
        self.timestamp = timestamp if timestamp else datetime.now()

        self.validate(score_type, score_value)

        self.score_value = score_value
        self.score_value_description = score_value_description

        if score_type not in get_args(ScoreType):
            raise ValueError(f"Score type {score_type} is not a valid score type.")

        self.score_type = score_type
        self.score_category = score_category
        self.score_rationale = score_rationale
        self.score_metadata = score_metadata or {}
        self.message_piece_id = message_piece_id
        self.objective = objective

        # Normalize to ScorerIdentifier (handles dict with deprecation warning)
        self.scorer_class_identifier = ScorerIdentifier.normalize(scorer_class_identifier)

    def get_value(self) -> bool | float:
        """
        Returns the value of the score based on its type.

        If the score type is "true_false", it returns True if the score value is "true" (case-insensitive),
        otherwise it returns False.

        If the score type is "float_scale", it returns the score value as a float.

        Raises:
            ValueError: If the score type is unknown.

        Returns:
            The value of the score based on its type.
        """
        if self.score_type == "true_false":
            return self.score_value.lower() == "true"
        elif self.score_type == "float_scale":
            return float(self.score_value)

        raise ValueError(f"Unknown scorer type: {self.score_type}")

    def validate(self, scorer_type: str, score_value: str) -> None:
        if scorer_type == "true_false" and str(score_value).lower() not in ["true", "false"]:
            raise ValueError(f"True False scorers must have a score value of 'true' or 'false' not {score_value}")
        elif scorer_type == "float_scale":
            try:
                score = float(score_value)
                if not (0 <= score <= 1):
                    raise ValueError(f"Float scale scorers must have a score value between 0 and 1. Got {score_value}")
            except ValueError:
                raise ValueError(f"Float scale scorers require a numeric score value. Got {score_value}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "score_value": self.score_value,
            "score_value_description": self.score_value_description,
            "score_type": self.score_type,
            "score_category": self.score_category,
            "score_rationale": self.score_rationale,
            "score_metadata": self.score_metadata,
            "scorer_class_identifier": self.scorer_class_identifier.to_dict() if self.scorer_class_identifier else None,
            "message_piece_id": str(self.message_piece_id),
            "timestamp": self.timestamp.isoformat(),
            "objective": self.objective,
        }

    def __str__(self) -> str:
        category_str = f": {', '.join(self.score_category) if self.score_category else ''}"
        if self.scorer_class_identifier:
            scorer_type = self.scorer_class_identifier.class_name or "Unknown"
            return f"{scorer_type}{category_str}: {self.score_value}"
        return f"{category_str}: {self.score_value}"

    __repr__ = __str__


@dataclass
class UnvalidatedScore:
    """
    Score is an object that validates all the fields. However, we need a common
    data class that can be used to store the raw score value before it is normalized and validated.
    """

    # The raw score value; has no scale. E.g. in likert could be 1-5
    raw_score_value: str

    score_value_description: str
    score_category: Optional[List[str]]
    score_rationale: str
    score_metadata: Optional[Dict[str, Union[str, int, float]]]
    scorer_class_identifier: ScorerIdentifier
    message_piece_id: uuid.UUID | str
    objective: Optional[str]
    id: Optional[uuid.UUID | str] = None
    timestamp: Optional[datetime] = None

    def to_score(self, *, score_value: str, score_type: ScoreType) -> Score:
        return Score(
            id=self.id,
            score_value=score_value,
            score_value_description=self.score_value_description,
            score_type=score_type,
            score_category=self.score_category,
            score_rationale=self.score_rationale,
            score_metadata=self.score_metadata,
            scorer_class_identifier=self.scorer_class_identifier,
            message_piece_id=self.message_piece_id,
            timestamp=self.timestamp,
            objective=self.objective,
        )
