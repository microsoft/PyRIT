# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from pyrit.models.identifiers.component_identifier import (
    ComponentIdentifier,  # noqa: TC001  (runtime-required by Pydantic field annotations)
)
from pyrit.models.score.scorable import (
    ContentScorable,
    MessageScorable,
    ScorableUnion,  # noqa: TC001  (runtime-required by Pydantic field annotations)
)

if TYPE_CHECKING:
    from pyrit.models.messages.message_piece import MessagePiece


def _digest_evidence(value: dict[str, object]) -> str:
    """
    Calculate a stable SHA-256 digest for canonical evidence fields.

    Returns:
        str: The lowercase SHA-256 digest.
    """
    serialized = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _message_piece_digest(piece: MessagePiece, *, include_id: bool) -> str:
    """
    Calculate a digest of the complete message state exposed to scorers.

    Returns:
        str: The lowercase SHA-256 digest.
    """
    value: dict[str, object] = piece.model_dump(mode="json")
    value["not_in_memory"] = piece.not_in_memory
    if not include_id:
        value.pop("id")
    return _digest_evidence(value)


def _response_piece_digest(piece: MessagePiece, *, include_id: bool) -> str:
    """
    Calculate a digest of the stable response fields consumed during replay.

    Returns:
        str: The lowercase SHA-256 digest.
    """
    value: dict[str, object] = {
        "role": piece.role,
        "original_value": piece.original_value,
        "original_value_data_type": piece.original_value_data_type,
        "original_value_sha256": piece.original_value_sha256,
        "converted_value": piece.converted_value,
        "converted_value_data_type": piece.converted_value_data_type,
        "converted_value_sha256": piece.converted_value_sha256,
        "response_error": piece.response_error,
        "structured_refusal": piece.structured_refusal,
        "prompt_metadata": piece.prompt_metadata,
    }
    if include_id:
        value["id"] = str(piece.id)
    return _digest_evidence(value)


def _content_scorable_digest(scorable: ContentScorable) -> str:
    """
    Calculate a digest of managed loose-content evidence.

    Returns:
        str: The lowercase SHA-256 digest.
    """
    return hashlib.sha256(scorable.value.encode("utf-8")).hexdigest()


class Acquisition(str, Enum):
    """Whether an LLM judgment response was acquired."""

    COMPLETE = "complete"
    ERROR = "error"


class LlmJudgmentObservationPayload(BaseModel):
    """References to the retained response from one LLM judgment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal["llm_judgment"] = "llm_judgment"
    scored_piece_id: uuid.UUID
    message_piece_ids: tuple[uuid.UUID, ...]
    message_piece_digests: tuple[str, ...]
    scored_evidence_digest: str = Field(min_length=64, max_length=64)
    expectation_fingerprint: str = Field(min_length=64, max_length=64)
    replay_contract_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)

    @field_validator("message_piece_ids")
    @classmethod
    def _validate_message_piece_ids(cls, value: tuple[uuid.UUID, ...]) -> tuple[uuid.UUID, ...]:
        """
        Require a non-empty, duplicate-free ordered reference list.

        Returns:
            tuple[uuid.UUID, ...]: The validated references.

        Raises:
            ValueError: If the list is empty or contains a duplicate.
        """
        if not value:
            raise ValueError("An LLM judgment payload must reference at least one message piece.")
        normalized = [str(piece_id) for piece_id in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("An LLM judgment payload must reference each message piece once.")
        return value

    @field_validator(
        "scored_evidence_digest",
        "expectation_fingerprint",
        "replay_contract_fingerprint",
    )
    @classmethod
    def _validate_fingerprint(cls, value: str | None) -> str | None:
        """
        Require a lowercase SHA-256 digest.

        Returns:
            str | None: The validated fingerprint.

        Raises:
            ValueError: If the value is not lowercase hexadecimal.
        """
        if value is not None and any(character not in "0123456789abcdef" for character in value):
            raise ValueError("Fingerprints must be lowercase SHA-256 hex digests.")
        return value

    @field_validator("message_piece_digests")
    @classmethod
    def _validate_message_piece_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """
        Require lowercase SHA-256 digests.

        Returns:
            tuple[str, ...]: The validated digests.

        Raises:
            ValueError: If a digest is not lowercase hexadecimal.
        """
        if any(
            len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest) for digest in value
        ):
            raise ValueError("Message piece digests must be lowercase SHA-256 hex digests.")
        return value

    @model_validator(mode="after")
    def _validate_message_piece_digest_count(self) -> LlmJudgmentObservationPayload:
        """
        Require one digest for each referenced response piece.

        Returns:
            LlmJudgmentObservationPayload: The validated payload.

        Raises:
            ValueError: If the reference and digest counts differ.
        """
        if len(self.message_piece_ids) != len(self.message_piece_digests):
            raise ValueError("An LLM judgment payload requires one digest per message piece.")
        return self


class Observation(BaseModel):
    """Managed LLM judgment evidence acquired about a scorable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_identifier: ComponentIdentifier
    acquisition: Acquisition
    observed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    scorable: ScorableUnion
    payload: LlmJudgmentObservationPayload
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_scored_piece(self) -> Observation:
        """
        Keep a message-backed judgment within its evidence anchor.

        Returns:
            Observation: The validated observation.

        Raises:
            ValueError: If the scored piece is not part of a message anchor.
        """
        if (
            isinstance(self.scorable, MessageScorable)
            and self.payload.scored_piece_id not in self.scorable.message_piece_ids
        ):
            raise ValueError("The scored piece must belong to the observation's MessageScorable.")
        return self
