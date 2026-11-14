# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend-neutral trace evidence for explicit trace scoring."""

from __future__ import annotations

import hashlib
from enum import Enum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from pyrit.models.score._trace_validation import (  # noqa: TC001 (runtime-required by Pydantic)
    SpanId,
    ToolName,
    TraceId,
)
from pyrit.models.score.scorable import TraceScorable  # noqa: TC001 (runtime-required by Pydantic)


class TraceSpanStatus(str, Enum):
    """The status of an observed execution attempt, not its scoring verdict."""

    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


class _TraceInterval(BaseModel):
    """An immutable identified interval with validated time bounds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trace_id: TraceId
    span_id: SpanId
    start_time: AwareDatetime
    end_time: AwareDatetime | None = None

    @model_validator(mode="after")
    def _validate_time_bounds(self) -> _TraceInterval:
        """
        Reject an end time that precedes the beginning of the interval.

        Returns:
            _TraceInterval: The validated interval.

        Raises:
            ValueError: If the end precedes the start.
        """
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("end_time must be greater than or equal to start_time.")
        return self


class TraceSpanEvent(BaseModel):
    """A transient timestamped event recorded on a span, such as a GenAI message or an exception."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    timestamp: AwareDatetime | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


class TraceSpan(_TraceInterval):
    """Transient backend-neutral span; raw attributes are not durable scoring evidence."""

    parent_span_id: SpanId | None = None
    name: str | None = None
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    events: tuple[TraceSpanEvent, ...] = ()
    status: TraceSpanStatus = TraceSpanStatus.UNSET
    status_message: str | None = None
    sampled: bool = True


class TraceCoverage(BaseModel):
    """Explicit capture completeness and known reasons coverage is incomplete."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    complete: bool = False
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_reasons(self) -> TraceCoverage:
        """
        Prevent completeness from contradicting known coverage limitations.

        Returns:
            TraceCoverage: The validated coverage.

        Raises:
            ValueError: If a reason is blank or contradicts complete coverage.
        """
        if self.complete and self.reasons:
            raise ValueError("Complete trace coverage cannot have incompleteness reasons.")
        if any(not reason.strip() for reason in self.reasons):
            raise ValueError("Trace coverage reasons must be nonempty.")
        return self


class TraceQuery(BaseModel):
    """A bounded request for the traces named by an immutable scoring scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: TraceScorable
    limit: int = Field(default=10000, gt=0, strict=True)


class TraceQueryResult(BaseModel):
    """
    A transient span snapshot and the client's explicit capture coverage.

    Set available=False only when no evidence can be retrieved, for example after
    retention expires. Pending capture uses available=True with incomplete coverage.
    If only some requested traces are available, return their spans with incomplete coverage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    available: bool = Field(default=True, strict=True)
    spans: tuple[TraceSpan, ...] = ()
    coverage: TraceCoverage = Field(default_factory=TraceCoverage)

    @model_validator(mode="after")
    def _validate_availability(self) -> TraceQueryResult:
        """
        Reject evidence or complete coverage when acquisition is unavailable.

        Returns:
            TraceQueryResult: The validated result.

        Raises:
            ValueError: If an unavailable result contains spans or claims completeness.
        """
        if not self.available and (self.spans or self.coverage.complete):
            raise ValueError("Unavailable trace results cannot contain spans or complete coverage.")
        return self


class ToolExecution(_TraceInterval):
    """Safe immutable name-only execution evidence; arguments and results are not retained."""

    name: ToolName
    parent_span_id: SpanId | None = None
    call_id: str | None = Field(default=None, min_length=1)
    status: TraceSpanStatus = TraceSpanStatus.UNSET


class ExecutionStepKind(str, Enum):
    """The role an execution span played, normalized across tracing conventions."""

    LLM = "llm"
    TOOL = "tool"
    AGENT = "agent"
    CHAIN = "chain"
    RETRIEVER = "retriever"
    EMBEDDING = "embedding"
    RERANKER = "reranker"
    GUARDRAIL = "guardrail"
    EVALUATOR = "evaluator"
    OTHER = "other"


class TraceContent(BaseModel):
    """
    Span content after any redaction: the complete value, or only its length and digest.

    ``text`` is the complete value, never a prefix. It is None only when the source was
    configured to withhold content; ``length`` and ``sha256`` still identify the value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str | None = None
    length: int = Field(ge=0, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    redacted: bool = Field(default=False, strict=True)

    @classmethod
    def of(cls, value: str, *, retain: bool = True, redacted: bool = False) -> TraceContent:
        """
        Describe a complete value, keeping its text only when ``retain`` is true.

        Returns:
            TraceContent: The content.
        """
        return cls(
            text=value if retain else None,
            length=len(value),
            sha256=hashlib.sha256(value.encode("utf-8")).hexdigest(),
            redacted=redacted,
        )

    @model_validator(mode="after")
    def _validate_complete_value(self) -> TraceContent:
        """
        Require retained text to be the complete value its length and digest describe.

        Returns:
            TraceContent: The validated content.

        Raises:
            ValueError: If retained text differs from the described value.
        """
        if self.text is not None and (
            len(self.text) != self.length or hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.sha256
        ):
            raise ValueError("Retained trace content must be the complete value its length and digest describe.")
        return self


class ExecutionStep(_TraceInterval):
    """
    One normalized execution span: a model call, tool call, agent step, or other operation.

    ``index`` is the step's stable position within its observation, used to cite it. Names
    and labels are kept exactly as observed, after any redaction.
    """

    index: int = Field(ge=0, strict=True)
    parent_span_id: SpanId | None = None
    kind: ExecutionStepKind
    name: str = Field(min_length=1)
    status: TraceSpanStatus = TraceSpanStatus.UNSET
    error_content: TraceContent | None = None
    model: str | None = None
    tool_name: ToolName | None = None
    call_id: str | None = Field(default=None, min_length=1)
    node_name: str | None = None
    input_content: TraceContent | None = None
    output_content: TraceContent | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
