# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from pyrit.models.identifiers.component_identifier import (
    ComponentIdentifier,  # noqa: TC001  (runtime-required by Pydantic field annotations)
)
from pyrit.models.literals import MEDIA_PATH_DATA_TYPES
from pyrit.models.score.scorable import (
    ContentEntryScorable,
    ContentScorable,
    MessageScorable,
    ScorableUnion,  # noqa: TC001  (runtime-required by Pydantic field annotations)
    TraceScorable,
)
from pyrit.models.score.trace import ToolExecution, TraceCoverage

if TYPE_CHECKING:
    from collections.abc import Mapping

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


def _resolved_scored_evidence_digest(
    *,
    scorable: ScorableUnion,
    scored_piece_id: uuid.UUID,
    scored_piece: MessagePiece | None = None,
    stored_content: tuple[ContentScorable, str] | None = None,
) -> str | None:
    """
    Hash supplied scored evidence without I/O; media capture remains deferred.

    Returns:
        str | None: The digest, or None for media evidence.

    Raises:
        ValueError: If evidence is missing, modified, or incompatible with the anchor.
    """
    if isinstance(scorable, MessageScorable):
        if scored_piece is None:
            raise ValueError(f"Scored message piece {scored_piece_id} is missing.")
        if scored_piece.id != scored_piece_id:
            raise ValueError(f"Prepared message piece {scored_piece.id} does not match scored piece {scored_piece_id}.")
        if scored_piece.converted_value_data_type in MEDIA_PATH_DATA_TYPES:
            return None
        return _message_piece_digest(scored_piece, include_id=False)
    if isinstance(scorable, ContentScorable):
        return None if scorable.data_type in MEDIA_PATH_DATA_TYPES else _content_scorable_digest(scorable)
    if isinstance(scorable, ContentEntryScorable):
        if stored_content is None or stored_content[0].data_type != scorable.data_type:
            raise ValueError(f"Scored content {scorable.content_id} is missing.")
        content, digest = stored_content
        if content.data_type in MEDIA_PATH_DATA_TYPES:
            return None
        if _content_scorable_digest(content) != digest:
            raise ValueError(f"Scored content {scorable.content_id} was modified.")
        return digest
    raise ValueError(f"Scorable type {type(scorable).__name__} cannot reference scorer target response evidence.")


class Acquisition(str, Enum):
    """Whether evidence was acquired and the selected scope was completely covered."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ScorerTargetResponsePayload(BaseModel):
    """References to the retained response from the scorer's target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal["scorer_target_response"] = "scorer_target_response"
    scored_piece_id: uuid.UUID
    message_piece_ids: tuple[uuid.UUID, ...]
    message_piece_digests: tuple[str, ...]
    scored_evidence_digest: str = Field(min_length=64, max_length=64)
    expectation_fingerprint: str = Field(min_length=64, max_length=64)
    replay_contract_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)

    def validate_scored_evidence(
        self,
        *,
        scorable: ScorableUnion,
        scored_piece: MessagePiece | None = None,
        stored_content: tuple[ContentScorable, str] | None = None,
    ) -> None:
        """
        Check supplied scored evidence against the retained digest.

        Raises:
            ValueError: If the evidence is missing, modified, or unsupported.
        """
        digest = _resolved_scored_evidence_digest(
            scorable=scorable,
            scored_piece_id=self.scored_piece_id,
            scored_piece=scored_piece,
            stored_content=stored_content,
        )
        if digest is None:
            raise ValueError("Media scorer target response observations are deferred.")
        if digest != self.scored_evidence_digest:
            raise ValueError(f"Observation references modified scored evidence for {self.scored_piece_id}.")

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
            raise ValueError("A scorer target response payload must reference at least one message piece.")
        normalized = [str(piece_id) for piece_id in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("A scorer target response payload must reference each message piece once.")
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
    def _validate_message_piece_digest_count(self) -> ScorerTargetResponsePayload:
        """
        Require one digest for each referenced response piece.

        Returns:
            ScorerTargetResponsePayload: The validated payload.

        Raises:
            ValueError: If the reference and digest counts differ.
        """
        if len(self.message_piece_ids) != len(self.message_piece_digests):
            raise ValueError("A scorer target response payload requires one digest per message piece.")
        return self


class ToolEventsObservationPayload(BaseModel):
    """An immutable allowlisted tool snapshot, with arguments and results not retained."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["tools"] = "tools"
    schema_version: Literal[1] = 1
    scope: TraceScorable
    events: tuple[ToolExecution, ...] = ()
    coverage: TraceCoverage = Field(default_factory=TraceCoverage)
    arguments_retained: Literal[False] = False

    @field_validator("arguments_retained", mode="before")
    @classmethod
    def _validate_arguments_retained(cls, value: object) -> Literal[False]:
        """
        Require the explicit no-argument-capture marker without coercion.

        Returns:
            Literal[False]: The supported argument-retention marker.

        Raises:
            ValueError: If the marker is not exactly false.
        """
        if value is not False:
            raise ValueError("Tool payload arguments_retained must be false; arguments are not captured.")
        return False

    @field_validator("schema_version", mode="before")
    @classmethod
    def _validate_schema_version(cls, value: Any) -> Any:
        """
        Require the exact supported schema version, without numeric coercion.

        Returns:
            Any: The supported schema version.

        Raises:
            ValueError: If the version is not exactly the supported integer.
        """
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported tool payload schema_version; expected 1.")
        return value

    @model_validator(mode="after")
    def _validate_scope_and_events(self) -> ToolEventsObservationPayload:
        """
        Keep event identities and completeness within the declared scope.

        Returns:
            ToolEventsObservationPayload: The validated snapshot.

        Raises:
            ValueError: If identities, scope, or completeness are inconsistent.
        """
        identities = [(event.trace_id, event.span_id) for event in self.events]
        if len(set(identities)) != len(identities):
            raise ValueError("Tool events must have unique trace/span identities.")
        if any(event.trace_id not in self.scope.trace_ids for event in self.events):
            raise ValueError("Tool events must belong to the declared trace scope.")
        if self.coverage.complete and any(event.end_time is None for event in self.events):
            raise ValueError("Complete coverage cannot contain unfinished tool events.")
        return self


ObservationPayload = Annotated[
    ScorerTargetResponsePayload | ToolEventsObservationPayload,
    Field(discriminator="kind"),
]


class Observation(BaseModel):
    """Managed evidence acquired about the caller's scorable anchor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    source_identifier: ComponentIdentifier
    acquisition: Acquisition
    observed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    scorable: ScorableUnion
    payload: ObservationPayload
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_target_response(self) -> Observation:
        """
        Check target response acquisition, anchor compatibility, and embedded evidence.

        Returns:
            Observation: The validated observation.

        Raises:
            ValueError: If the acquisition, anchor, or embedded evidence is invalid.
        """
        if not isinstance(self.payload, ScorerTargetResponsePayload):
            return self
        if self.acquisition not in (Acquisition.COMPLETE, Acquisition.ERROR):
            raise ValueError("Scorer target response observations require complete or error acquisition.")
        if not isinstance(self.scorable, (MessageScorable, ContentScorable, ContentEntryScorable)):
            raise ValueError("Scorer target response observations must refer to message or content evidence.")
        if isinstance(self.scorable, ContentScorable):
            self.payload.validate_scored_evidence(scorable=self.scorable)
        elif isinstance(self.scorable, ContentEntryScorable) and self.scorable.data_type in MEDIA_PATH_DATA_TYPES:
            raise ValueError("Media scorer target response observations are deferred.")
        if (
            isinstance(self.scorable, MessageScorable)
            and self.payload.scored_piece_id not in self.scorable.message_piece_ids
        ):
            raise ValueError("The scored piece must belong to the observation's MessageScorable.")
        return self

    @model_validator(mode="after")
    def _validate_tool_acquisition(self) -> Observation:
        """
        Require acquisition status to agree with the retained tool snapshot.

        Returns:
            Observation: The validated observation.

        Raises:
            ValueError: If acquisition, scope, or coverage are inconsistent.
        """
        if not isinstance(self.payload, ToolEventsObservationPayload):
            return self
        if not isinstance(self.scorable, TraceScorable) or self.scorable != self.payload.scope:
            raise ValueError("Tool observations require a TraceScorable matching their payload scope.")
        if (self.acquisition is Acquisition.COMPLETE) != self.payload.coverage.complete:
            raise ValueError("Tool acquisition and trace coverage completeness must agree.")
        if self.acquisition is Acquisition.UNAVAILABLE and self.payload.events:
            raise ValueError("Unavailable tool acquisition cannot contain events.")
        return self

    @property
    def response_message_piece_ids(self) -> tuple[uuid.UUID, ...]:
        """The ordered message references retained by this payload."""
        return self.payload.message_piece_ids if isinstance(self.payload, ScorerTargetResponsePayload) else ()

    @property
    def scored_message_piece_id(self) -> uuid.UUID | None:
        """The exact scored message reference, distinct from a score's canonical anchor."""
        if isinstance(self.payload, ScorerTargetResponsePayload) and isinstance(self.scorable, MessageScorable):
            return self.payload.scored_piece_id
        return None

    @property
    def evidence_message_piece_ids(self) -> tuple[uuid.UUID, ...]:
        """All message references needed to validate this observation."""
        scored = self.scored_message_piece_id
        response_ids = self.response_message_piece_ids
        return response_ids if scored is None or scored in response_ids else (*response_ids, scored)

    @property
    def scorable_content_id(self) -> uuid.UUID | None:
        """The stored content reference, if this observation has one."""
        return self.scorable.content_id if isinstance(self.scorable, ContentEntryScorable) else None

    def validate_evidence(
        self,
        *,
        message_pieces: Mapping[uuid.UUID, MessagePiece],
        stored_content: tuple[ContentScorable, str] | None = None,
    ) -> None:
        """
        Validate supplied canonical evidence without loading or changing it.

        Raises:
            ValueError: If scored or response evidence is missing, modified, or unsupported.
        """
        if isinstance(self.payload, ToolEventsObservationPayload):
            return
        payload = self.payload
        payload.validate_scored_evidence(
            scorable=self.scorable,
            scored_piece=message_pieces.get(payload.scored_piece_id),
            stored_content=stored_content,
        )
        for piece_id, expected_digest in zip(payload.message_piece_ids, payload.message_piece_digests, strict=True):
            piece = message_pieces.get(piece_id)
            if piece is None or _response_piece_digest(piece, include_id=True) != expected_digest:
                raise ValueError(f"Observation {self.id} references missing or modified message pieces: {piece_id}.")
