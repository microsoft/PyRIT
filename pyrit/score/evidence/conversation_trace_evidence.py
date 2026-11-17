# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Execution trace evidence that a conversation judge reads with the transcript."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from pyrit.models import Acquisition, ExecutionStep, ExecutionTraceObservationPayload, Observation, TraceSpanStatus
from pyrit.score.observation.execution import _acquire_once_async
from pyrit.score.observation.message_trace_resolver import (
    RequestTraceLink,
    resolve_conversation_trace_links_async,
    trace_scope_from_links,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.memory import MemoryInterface
    from pyrit.models import ComponentIdentifier, Score, TraceScorable
    from pyrit.score.observation.observation_source import ObservationSource


class TraceEvidenceRequirement(str, Enum):
    """
    The execution evidence a conversation judgment requires before its judge is called.

    ``COMPLETE`` requires every outbound request to be linked to a trace with complete
    coverage. ``NONE`` judges regardless and includes whatever execution evidence is available.
    """

    COMPLETE = "complete"
    NONE = "none"


@dataclass(frozen=True, kw_only=True)
class AcquiredTrace:
    """Execution evidence acquired for one conversation, with the reasons it is incomplete."""

    links: tuple[RequestTraceLink, ...]
    observation: Observation | None
    reasons: tuple[str, ...]

    @property
    def payload(self) -> ExecutionTraceObservationPayload | None:
        """The acquired execution snapshot, if any."""
        if self.observation is None or not isinstance(self.observation.payload, ExecutionTraceObservationPayload):
            return None
        return self.observation.payload

    @property
    def complete(self) -> bool:
        """Whether every request is linked to a completely acquired trace."""
        return (
            self.observation is not None
            and self.observation.acquisition is Acquisition.COMPLETE
            and bool(self.links)
            and all(link.linked for link in self.links)
        )

    @property
    def available(self) -> bool:
        """Whether any execution steps were acquired."""
        payload = self.payload
        return payload is not None and (bool(payload.steps) or self.complete)

    @property
    def coverage_label(self) -> str:
        """``complete``, ``partial``, or ``unavailable``."""
        return "complete" if self.complete else "partial" if self.available else "unavailable"


@dataclass(frozen=True, kw_only=True)
class TraceRendering:
    """Execution blocks keyed by the request sequence they follow, plus summary framing."""

    header: str
    blocks: dict[int, str]
    request_sequences: frozenset[int]
    rendered_step_indexes: frozenset[int]
    truncated: bool


class ConversationTraceEvidence:
    """
    Acquire, gate on, render, and match the execution traces behind a conversation.

    The traces linked to the conversation's outbound requests are acquired once per scoring
    operation. The judge sees them as fenced step blocks after the turns they produced, with
    step IDs such as ``[T3]`` it can cite, within a character budget. The saved evidence stays
    complete; only what the judge is shown is bounded.
    """

    RENDERING_VERSION: ClassVar[int] = 1
    #: The judge sees at most this many characters of each saved input, output, or error value.
    MAX_RENDERED_CONTENT_CHARS: ClassVar[int] = 4000
    #: The judge sees at most this many characters of each step name, model, or node label.
    MAX_RENDERED_LABEL_CHARS: ClassVar[int] = 200
    #: Step citations such as ``[T3]``; longer numbers cannot name a step and are ignored.
    _CITATION: ClassVar[re.Pattern[str]] = re.compile(r"\[T(\d{1,9})\]")
    _NO_SPANS_LINE: ClassVar[str] = "  (no spans recorded)\n"

    def __init__(
        self,
        *,
        source: ObservationSource[TraceScorable],
        requirement: TraceEvidenceRequirement = TraceEvidenceRequirement.NONE,
        max_rendered_chars: int = 60000,
    ) -> None:
        """
        Configure the evidence a conversation judge reads.

        Args:
            source (ObservationSource[TraceScorable]): Reads the traces linked to the requests.
            requirement (TraceEvidenceRequirement): Evidence required before the judge is called.
                Defaults to NONE.
            max_rendered_chars (int): Character budget for rendered steps. Defaults to 60000.

        Raises:
            ValueError: If the requirement is unknown or the budget is not positive.
        """
        if max_rendered_chars < 1:
            raise ValueError("max_rendered_trace_chars must be positive.")
        self._source = source
        self._requirement = TraceEvidenceRequirement(requirement)
        self._max_rendered_chars = max_rendered_chars

    @property
    def source_identifier(self) -> ComponentIdentifier:
        """The trace source's identity."""
        return self._source.get_identifier()

    def get_identifier_params(self) -> dict[str, object]:
        """
        Return the settings that shape what the judge sees and when it runs.

        Returns:
            dict[str, object]: Identity parameters for the scorer that uses this evidence.
        """
        return {
            "trace_rendering_version": self.RENDERING_VERSION,
            "trace_requirement": self._requirement.value,
            "max_rendered_trace_chars": self._max_rendered_chars,
        }

    async def acquire_async(self, *, conversation_id: str, memory: MemoryInterface) -> AcquiredTrace:
        """
        Acquire execution evidence for every outbound request in the conversation.

        Returns:
            AcquiredTrace: The links, the acquired observation if any, and coverage gaps.

        Raises:
            ValueError: If the trace source returns evidence for a different scope or kind.
        """
        links = await resolve_conversation_trace_links_async(conversation_id=conversation_id, memory=memory)
        reasons = ["unlinked_requests"] if any(not link.linked for link in links) else []
        scope = trace_scope_from_links(links)
        if scope is None:
            return AcquiredTrace(links=links, observation=None, reasons=(*reasons, "no_request_trace_links"))
        source = self._source
        key = ("trace", source.get_identifier().hash, scope.trace_ids)
        observation = await _acquire_once_async(key=key, acquire=lambda: source.acquire_async(scorable=scope))
        if (
            observation.scorable != scope
            or not isinstance(observation.payload, ExecutionTraceObservationPayload)
            or observation.payload.scope != scope
        ):
            raise ValueError("Trace source returned incompatible evidence or scope.")
        reasons.extend(observation.payload.coverage.reasons)
        return AcquiredTrace(links=links, observation=observation, reasons=tuple(dict.fromkeys(reasons)))

    def get_unmet_requirement(self, trace: AcquiredTrace) -> str | None:
        """
        Return why the configured requirement is not met, or None when it is.

        Returns:
            str | None: The unmet requirement's reason.
        """
        if self._requirement is TraceEvidenceRequirement.COMPLETE and not trace.complete:
            return "trace_not_complete"
        return None

    def describe_unmet_requirement(self, trace: AcquiredTrace) -> str:
        """
        Explain why the judge was not called.

        Returns:
            str: The rationale for the undetermined result.
        """
        return (
            f"The judge was not called: this scorer requires {self._requirement.value} execution "
            f"evidence, but the conversation's trace coverage is {trace.coverage_label} "
            f"({', '.join(trace.reasons) or 'no reasons reported'})."
        )

    def get_metadata(self, *, trace: AcquiredTrace, rendering: TraceRendering) -> dict[str, str | int | float]:
        """
        Record the coverage the judge saw.

        Returns:
            dict[str, str | int | float]: Coverage, reasons, step count, requirement, and truncation.
        """
        payload = trace.payload
        metadata: dict[str, str | int | float] = {
            "trace_coverage": trace.coverage_label,
            "trace_reasons": ",".join(trace.reasons),
            "trace_step_count": len(payload.steps) if payload else 0,
            "trace_requirement": self._requirement.value,
        }
        if rendering.truncated:
            metadata["trace_rendering_truncated"] = "true"
        return metadata

    def get_citation_metadata(self, *, rationale: str, rendering: TraceRendering) -> dict[str, str]:
        """
        Record the steps a rationale cited and any it cited that the judge was not shown.

        Returns:
            dict[str, str]: Citation metadata, empty when nothing was cited.
        """
        cited = sorted({int(digits) for digits in self._CITATION.findall(rationale)})
        if not cited:
            return {}
        unknown = [index for index in cited if index not in rendering.rendered_step_indexes]
        metadata = {"trace_cited_steps": json.dumps(cited)}
        if unknown:
            metadata["trace_citation_warnings"] = json.dumps([f"unknown_step:T{index}" for index in unknown])
        return metadata

    async def filter_matching_scores_async(
        self, *, scores: Sequence[Score], conversation_id: str, memory: MemoryInterface
    ) -> list[Score]:
        """
        Keep results whose linked execution snapshot matches the evidence acquired now.

        Matching is intentionally strict: a stored result is reused only when the evidence acquired
        now equals the evidence it was judged with. Once the capture is closed or the trace evicted,
        or in a new process, the evidence differs, so the conversation is judged again with what is
        available now; earlier results stay in memory unchanged. A result made without execution
        evidence matches only while none is available.

        Returns:
            list[Score]: Results whose execution evidence is unchanged.
        """
        current = (await self.acquire_async(conversation_id=conversation_id, memory=memory)).observation
        stored = await memory.get_observations_async(
            observation_ids=list({observation_id for score in scores for observation_id in score.observation_ids})
        )
        traces = {
            observation.id: observation
            for observation in stored
            if isinstance(observation.payload, ExecutionTraceObservationPayload)
        }
        matched = []
        for score in scores:
            linked = [traces[observation_id] for observation_id in score.observation_ids if observation_id in traces]
            if current is None:
                if not linked:
                    matched.append(score)
                continue
            if any(
                observation.acquisition == current.acquisition
                and observation.scorable == current.scorable
                and observation.source_identifier == current.source_identifier
                and observation.payload == current.payload
                for observation in linked
            ):
                matched.append(score)
        return matched

    def render(self, trace: AcquiredTrace) -> TraceRendering:
        """
        Render execution steps into one fenced block per linked request trace.

        Returns:
            TraceRendering: The header, per-request blocks, and rendering statistics.
        """
        payload = trace.payload
        reasons = f" ({', '.join(trace.reasons)})" if trace.reasons else ""
        steps = payload.steps if payload else ()
        header = (
            "[BEGIN EXECUTION EVIDENCE]\n"
            f"  coverage: {trace.coverage_label}{reasons}\n"
            f"  steps: {len(steps)}\n"
            "[END EXECUTION EVIDENCE]\n"
        )
        budget = [self._max_rendered_chars]
        rendered: set[int] = set()
        truncated = [False]
        blocks: dict[int, str] = {}
        emitted: set[str] = set()
        for link in trace.links:
            if link.trace_id is None or link.trace_id in emitted:
                continue
            emitted.add(link.trace_id)
            trace_steps = [step for step in steps if step.trace_id == link.trace_id]
            body = self._render_steps(steps=trace_steps, budget=budget, rendered=rendered, truncated=truncated)
            fence = f"EXECUTION TRACE {len(emitted)}"
            blocks[link.sequence] = f"[BEGIN {fence}]\n{body or self._NO_SPANS_LINE}[END {fence}]\n"
        return TraceRendering(
            header=header,
            blocks=blocks,
            request_sequences=frozenset(link.sequence for link in trace.links),
            rendered_step_indexes=frozenset(rendered),
            truncated=truncated[0],
        )

    def _render_steps(
        self, *, steps: list[ExecutionStep], budget: list[int], rendered: set[int], truncated: list[bool]
    ) -> str:
        """
        Render steps depth-first by parent, spending a shared character budget.

        Steps unreachable from a root, because their parent links form a cycle, are rendered as
        roots so no step is hidden. Each content value is previewed up to
        ``MAX_RENDERED_CONTENT_CHARS``. Content is dropped first when the budget runs low; then
        remaining steps are omitted. Any preview cut or omission marks the rendering truncated.

        Returns:
            str: The indented step lines.
        """
        span_ids = {step.span_id for step in steps}
        children: dict[str | None, list[ExecutionStep]] = {}
        for step in steps:
            parent = step.parent_span_id if step.parent_span_id in span_ids else None
            children.setdefault(parent, []).append(step)
        ordered: list[tuple[ExecutionStep, int]] = []
        visited: set[int] = set()
        for start in [*children.get(None, []), *steps]:
            stack = [(start, 0)]
            while stack:
                step, depth = stack.pop()
                if step.index in visited:
                    continue
                visited.add(step.index)
                ordered.append((step, depth))
                stack.extend((child, depth + 1) for child in reversed(children.get(step.span_id, [])))
        lines = []
        for position, (step, depth) in enumerate(ordered):
            text = self._render_step(step=step, depth=depth, include_content=True)
            contents = (step.error_content, step.input_content, step.output_content)
            labels = (step.name, step.model, step.node_name)
            if any(len(value) > self.MAX_RENDERED_CONTENT_CHARS for value in contents if value) or any(
                len(" ".join(value.split())) > self.MAX_RENDERED_LABEL_CHARS for value in labels if value
            ):
                truncated[0] = True
            if len(text) > budget[0]:
                truncated[0] = True
                text = self._render_step(step=step, depth=depth, include_content=False)
            if len(text) > budget[0]:
                lines.append(f"  ... {len(ordered) - position} more steps omitted (rendering budget)\n")
                break
            budget[0] -= len(text)
            rendered.add(step.index)
            lines.append(text)
        return "".join(lines)

    @classmethod
    def _render_step(cls, *, step: ExecutionStep, depth: int, include_content: bool) -> str:
        indent = "  " * (depth + 1)
        details = [f"status={step.status.value}"] if step.status is not TraceSpanStatus.UNSET else []
        details += [
            f"{label}={cls._render_label(value)}"
            for label, value in (("model", step.model), ("node", step.node_name))
            if value
        ]
        if step.input_tokens is not None or step.output_tokens is not None:
            details.append(f"tokens={step.input_tokens or 0}/{step.output_tokens or 0}")
        lines = [
            f"{indent}T{step.index} {step.kind.value} {cls._render_label(step.name)}"
            + "".join(f" {d}" for d in details)
            + "\n"
        ]
        for label, content in (
            ("error", step.error_content),
            ("input", step.input_content),
            ("output", step.output_content),
        ):
            if content is not None:
                lines.append(cls._render_content(label=label, content=content, indent=indent, full=include_content))
        return "".join(lines)

    @classmethod
    def _render_label(cls, value: str) -> str:
        """
        Flatten a saved label to one line and bound its preview.

        Returns:
            str: The one-line label preview.
        """
        label = " ".join(value.split())
        limit = cls.MAX_RENDERED_LABEL_CHARS
        return label if len(label) <= limit else f"{label[:limit]}..."

    @classmethod
    def _render_content(cls, *, label: str, content: str, indent: str, full: bool) -> str:
        if not full:
            return f"{indent}  {label}: ({len(content)} chars omitted for length)\n"
        limit = cls.MAX_RENDERED_CONTENT_CHARS
        note = f" (first {limit} of {len(content)} chars)" if len(content) > limit else ""
        body = "".join(f"{indent}    {line}" for line in content[:limit].splitlines(keepends=True))
        return f"{indent}  {label}{note}:\n{body}\n"
