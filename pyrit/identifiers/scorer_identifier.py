# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type, cast

from pyrit.identifiers.identifier import MAX_STORAGE_LENGTH, Identifier
from pyrit.models.score import ScoreType


@dataclass(frozen=True)
class ScorerIdentifier(Identifier):
    """
    Identifier for Scorer instances.

    This frozen dataclass extends Identifier with scorer-specific fields.
    Long prompt templates are automatically truncated for storage display.
    """

    scorer_type: ScoreType = "unknown"
    """The type of scorer ("true_false", "float_scale", or "unknown")."""

    system_prompt_template: Optional[str] = field(default=None, metadata={MAX_STORAGE_LENGTH: 100})
    """The system prompt template used by the scorer. Truncated for storage if > 100 characters."""

    user_prompt_template: Optional[str] = field(default=None, metadata={MAX_STORAGE_LENGTH: 100})
    """The user prompt template used by the scorer. Truncated for storage if > 100 characters."""

    sub_identifier: Optional[List["ScorerIdentifier"]] = None
    """List of sub-scorer identifiers for composite scorers."""

    target_info: Optional[Dict[str, Any]] = None
    """Information about the prompt target used by the scorer."""

    score_aggregator: Optional[str] = None
    """The name of the score aggregator function."""

    scorer_specific_params: Optional[Dict[str, Any]] = None
    """Additional scorer-specific parameters."""

    @classmethod
    def from_dict(cls: Type["ScorerIdentifier"], data: dict[str, Any]) -> "ScorerIdentifier":
        """
        Create a ScorerIdentifier from a dictionary (e.g., retrieved from database).

        Extends the base Identifier.from_dict() to recursively reconstruct
        nested ScorerIdentifier objects in sub_identifier.

        Args:
            data: The dictionary representation.

        Returns:
            ScorerIdentifier: A new ScorerIdentifier instance.
        """
        # Create a mutable copy
        data = dict(data)

        # Recursively reconstruct sub_identifier if present
        if "sub_identifier" in data and data["sub_identifier"] is not None:
            data["sub_identifier"] = [
                ScorerIdentifier.from_dict(sub) if isinstance(sub, dict) else sub for sub in data["sub_identifier"]
            ]

        # Delegate to parent class for standard processing
        result = Identifier.from_dict.__func__(cls, data)  # type: ignore[attr-defined]
        return cast(ScorerIdentifier, result)
