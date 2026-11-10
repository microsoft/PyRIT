# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from pyrit.exceptions import ScorerLLMResponseBlockedException
from pyrit.models import (
    Acquisition,
    ChatMessageRole,
    ComponentIdentifier,
    ContentScorable,
    ConversationObservationPayload,
    ConversationScorable,
    ExecutionStep,
    ExecutionTraceObservationPayload,
    Message,
    MessagePiece,
    MessageScorable,
    Observation,
    PromptDataType,
    Scorable,
    Score,
    ScoringExpectation,
    TraceContent,
    TraceScorable,
    TraceSpanStatus,
)
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer, MessageFloatScaleScorer
from pyrit.score.message_scorer import MessageScorer
from pyrit.score.observation import ConversationSource
from pyrit.score.observation.execution import (
    _acquire_once_async,
    _collect_observation,
    _ObservationEvidenceResolver,
)
from pyrit.score.observation.message_trace_resolver import (
    RequestTraceLink,
    resolve_conversation_trace_links_async,
    trace_scope_from_links,
)
from pyrit.score.observation.observation_source import ObservationSource
from pyrit.score.scorer import Scorer, SnapshotParticipation, _read_snapshot_participation
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer, TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget


class TraceEvidenceRequirement(str, Enum):
    """
    The execution evidence a conversation judgment requires before its judge is called.

    ``COMPLETE`` requires every outbound request to be linked to a trace with complete
    coverage. ``AVAILABLE`` requires some acquired execution steps, even if partial.
    ``NONE`` judges regardless and includes whatever execution evidence is available.
    """

    COMPLETE = "complete"
    AVAILABLE = "available"
    NONE = "none"


@dataclass(frozen=True, kw_only=True)
class _TraceEvidence:
    """Execution evidence acquired for one conversation, with the reasons it is incomplete."""

    links: tuple[RequestTraceLink, ...]
    observation: Observation | None
    reasons: tuple[str, ...]

    @property
    def payload(self) -> ExecutionTraceObservationPayload | None:
        if self.observation is None or not isinstance(self.observation.payload, ExecutionTraceObservationPayload):
            return None
        return self.observation.payload

    @property
    def complete(self) -> bool:
        return (
            self.observation is not None
            and self.observation.acquisition is Acquisition.COMPLETE
            and bool(self.links)
            and all(link.linked for link in self.links)
        )

    @property
    def available(self) -> bool:
        payload = self.payload
        return payload is not None and (bool(payload.steps) or self.complete)

    @property
    def coverage_label(self) -> str:
        return "complete" if self.complete else "partial" if self.available else "unavailable"


@dataclass(frozen=True, kw_only=True)
class _TraceRendering:
    """Execution blocks keyed by the request sequence they follow, plus summary framing."""

    header: str
    blocks: dict[int, str]
    request_sequences: frozenset[int]
    rendered_step_indexes: frozenset[int]
    truncated: bool


class ConversationScorer(MessageScorer, ABC):
    """
    Scorer that evaluates entire conversation history rather than individual messages.

    This scorer wraps a float-scale or true/false scorer that supports text
    ``ContentScorable`` evidence and evaluates the full conversation context.

    The ConversationScorer dynamically inherits from the same base class as the wrapped scorer,
    ensuring proper type compatibility.

    Note: This class cannot be instantiated directly. Use create_conversation_scorer() factory instead.
    """

    SUPPORTED_SCORABLE_TYPES = (MessageScorable, ConversationScorable)
    _REQUIRES_CONVERSATION_HISTORY = True
    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(
        supported_data_types=["text"],
        enforce_all_pieces_valid=False,
    )
    _source: ObservationSource[ConversationScorable]
    _excluded_data_types: tuple[PromptDataType, ...] = ()
    _trace_source: ObservationSource[TraceScorable] | None = None
    _trace_requirement: TraceEvidenceRequirement = TraceEvidenceRequirement.NONE
    _max_rendered_trace_chars: int = 60000

    TRACE_RENDERING_VERSION: ClassVar[int] = 2
    #: Metadata key on an undetermined result whose judge was not called because the configured
    #: trace requirement was not met. Its value is the reason.
    EVIDENCE_REQUIREMENT_UNMET_KEY: ClassVar[str] = "evidence_requirement_unmet"
    _TRACE_CITATION: ClassVar[re.Pattern[str]] = re.compile(r"\[T(\d+)\]")
    _NO_SPANS_LINE: ClassVar[str] = "  (no spans recorded)\n"

    def get_chat_target(self) -> "PromptTarget | None":
        """
        Return the wrapped scorer's target.

        Returns:
            PromptTarget | None: The configured scoring target, if any.
        """
        return self._get_wrapped_scorer().get_chat_target()

    async def find_matching_scores_async(
        self,
        *,
        scores: Sequence[Score],
        scorable: Scorable,
        expectation: ScoringExpectation | None = None,
    ) -> list[Score]:
        """
        Match the complete ordered snapshot that a fresh conversation evaluation would acquire.

        Returns:
            Scores for the exact captured evidence and effective expectation, newest first.
        """
        conversation_scorable = await self._get_conversation_scorable_async(scorable=scorable)
        if conversation_scorable is None:
            return []
        prepared = self.prepare_expectation(expectation=expectation)
        candidates = self.find_matching_scores(scores=scores, scorable=conversation_scorable, expectation=prepared)
        if not candidates:
            return []
        return await self._filter_matching_scores_by_snapshot_async(
            scores=candidates,
            scorable=conversation_scorable,
            expectation=prepared,
            used_observation_ids={str(score.id): set() for score in candidates},
            participation_by_score={str(score.id): _read_snapshot_participation(score) for score in candidates},
        )

    async def _get_conversation_scorable_async(self, *, scorable: Scorable) -> ConversationScorable | None:
        """
        Resolve a message trigger to its single complete conversation scope.

        Returns:
            ConversationScorable | None: The conversation scope, or None for an invalid trigger.
        """
        if isinstance(scorable, MessageScorable):
            messages = await self._memory.get_message_pieces_async(prompt_ids=list(scorable.message_piece_ids))
            if {piece.id for piece in messages} != set(scorable.message_piece_ids):
                return None
            conversation_ids = {piece.conversation_id for piece in messages}
            if len(conversation_ids) != 1:
                return None
            conversation_id = messages[0].conversation_id
            if conversation_id is None:
                return None
            scorable = ConversationScorable(conversation_id=conversation_id)
        if not isinstance(scorable, ConversationScorable):
            return None
        return scorable

    async def _filter_matching_scores_by_snapshot_async(
        self,
        *,
        scores: Sequence[Score],
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        used_observation_ids: dict[str, set[str]],
        participation_by_score: dict[str, SnapshotParticipation | None],
    ) -> list[Score]:
        """
        Require an unused linked observation of this branch's exact current conversation.

        Returns:
            list[Score]: Root candidates with a complete current snapshot for this participant.
        """
        conversation_scorable = await self._get_conversation_scorable_async(scorable=scorable)
        if conversation_scorable is None:
            return []
        current = await self._source.acquire_async(scorable=conversation_scorable)
        if current.acquisition != Acquisition.COMPLETE or not isinstance(
            current.payload, ConversationObservationPayload
        ):
            return []
        observation_ids = {
            observation_id
            for score in scores
            for observation_id in score.observation_ids
            if str(observation_id) not in used_observation_ids.setdefault(str(score.id), set())
        }
        if not observation_ids:
            return []
        observations = await self._memory.get_observations_async(observation_ids=list(observation_ids))
        matching_ids = {
            observation.id
            for observation in observations
            if observation.acquisition == Acquisition.COMPLETE
            and observation.scorable == conversation_scorable
            and observation.source_identifier == current.source_identifier
            and observation.payload == current.payload
        }
        matched_scores = []
        for score in scores:
            available_ids = {
                observation_id
                for observation_id in score.observation_ids
                if observation_id in matching_ids and str(observation_id) not in used_observation_ids[str(score.id)]
            }
            if available_ids:
                used_observation_ids[str(score.id)].add(str(min(available_ids, key=str)))
                matched_scores.append(score)
        if self._trace_source is None or not matched_scores:
            return matched_scores
        return await self._filter_matching_scores_by_trace_async(
            scores=matched_scores, conversation_id=conversation_scorable.conversation_id
        )

    async def _filter_matching_scores_by_trace_async(
        self, *, scores: Sequence[Score], conversation_id: str
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
        current = (await self._acquire_trace_evidence_async(conversation_id=conversation_id)).observation
        stored = await self._memory.get_observations_async(
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

    async def _score_message_scorable_async(
        self,
        *,
        scorable: Scorable,
        expectation: ScoringExpectation | None,
        infer_objective_from_request: bool,
        role_filter: ChatMessageRole | None,
        skip_on_error_result: bool,
    ) -> list[Score]:
        if isinstance(scorable, ConversationScorable):
            if infer_objective_from_request or role_filter is not None or skip_on_error_result:
                raise ValueError("Message-only scoring options cannot be used with a ConversationScorable.")
            expectation = self.prepare_expectation(expectation=expectation)
            scores = await self._score_conversation_async(scorable=scorable, expectation=expectation)
            self._stamp_scored_expectation(scores=scores, expectation=expectation)
            return scores
        return await super()._score_message_scorable_async(
            scorable=scorable,
            expectation=expectation,
            infer_objective_from_request=infer_objective_from_request,
            role_filter=role_filter,
            skip_on_error_result=skip_on_error_result,
        )

    async def _finalize_message_scores_async(
        self,
        *,
        message: Message,
        scores: list[Score],
        anchor: Scorable | None,
        expectation: ScoringExpectation | None,
    ) -> None:
        conversation_anchors = [score.scorable for score in scores]
        await super()._finalize_message_scores_async(
            message=message, scores=scores, anchor=anchor, expectation=expectation
        )
        for score, conversation_anchor in zip(scores, conversation_anchors, strict=True):
            if isinstance(conversation_anchor, ConversationScorable):
                score.scorable = conversation_anchor

    def _get_child_scorers(self) -> tuple[Scorer, ...]:
        """Return the scorer that evaluates the conversation text."""
        return (self._get_wrapped_scorer(),)

    def _build_scoring_message(self, *, message: Message) -> Message | None:
        """
        Keep the trigger that identifies the conversation to acquire.

        The trigger content is not sent to the child scorer. ``_score_prepared_message_async``
        replaces it with a text view of the full conversation. Overriding this hook keeps an
        unreadable trigger, because the conversation behind it is still there to read.

        Returns:
            Message | None: The trigger message, or None if it has no pieces.
        """
        return message if message.message_pieces else None

    def _reads_any_role(self, *, message: Message, anchor: Scorable | None) -> bool:
        """
        Defer role policy until the conversation locator has acquired its evidence.

        Returns:
            bool: True because the trigger identifies history; it is not the evidence itself.
        """
        return True

    def _build_fallback_score(self, *, message: Message, objective: str | None) -> list[Score]:
        """
        Return ``[]`` when the conversation trigger does not yield applicable evidence.

        Returns:
            list[Score]: Always ``[]``.
        """
        return []

    def _validate_scoring_message(self, *, message: Message, objective: str | None) -> None:
        """Skip message validation because the trigger is only a conversation locator."""

    async def _score_prepared_message_async(
        self,
        *,
        message: Message,
        expectation: ScoringExpectation | None,
    ) -> list[Score]:
        """
        Scores the entire conversation history by concatenating all messages and passing to the wrapped scorer.

        The synthetic conversation Message is always built as ``text`` regardless of the
        triggering piece's data type or error state. Errors from individual turns are
        preserved within the rendered text (either as the partial content, or as the rendered
        error JSON when ``should_score_blocked_content`` is turned off). This ensures the wrapped
        scorer's text-only validator accepts the synthetic message and scores the full
        conversation, even when the triggering turn was blocked or errored; the wrapped
        scorer returns ``[]`` when the rendered conversation is not applicable.

        The wrapped scorer is invoked through its non-persisting nested path. The outer
        scoring operation persists the results once, anchored to the conversation, with
        the real trigger-message link retained for compatibility.

        Args:
            message (Message): A message from the conversation to be scored.
                The conversation ID from the first message piece is used to retrieve the full conversation from memory.
            expectation (ScoringExpectation | None): What the wrapped scorer should look for.

        Returns:
            list[Score]: The wrapped scorer's completed or undetermined results, or ``[]``
                when no applicable conversation evidence or child score exists.

        Raises:
            ValueError: If conversation with the given ID is not found in memory.
        """
        if not message.message_pieces:
            return []

        conversation_id = message.message_pieces[0].conversation_id
        if not conversation_id:
            raise ValueError(f"Conversation with ID {conversation_id} not found in memory.")
        scores = await self._score_conversation_async(
            scorable=ConversationScorable(conversation_id=conversation_id),
            expectation=expectation,
        )
        trigger_piece = message.message_pieces[0]
        for score in scores:
            score.message_piece_id = trigger_piece.id or trigger_piece.original_prompt_id
        return scores

    async def _score_conversation_async(
        self, *, scorable: ConversationScorable, expectation: ScoringExpectation | None
    ) -> list[Score]:
        observation = await self._source.acquire_async(scorable=scorable)
        if observation.scorable != scorable or not isinstance(observation.payload, ConversationObservationPayload):
            raise ValueError("Conversation source returned incompatible evidence or scope.")
        pieces = await _ObservationEvidenceResolver(memory=self._memory).resolve_async(observation=observation)
        if not isinstance(pieces, tuple):
            raise TypeError("Conversation evidence must resolve to an ordered tuple of message pieces.")
        trace = (
            await self._acquire_trace_evidence_async(conversation_id=scorable.conversation_id)
            if self._trace_source is not None
            else None
        )
        rendering = self._render_trace(trace) if trace is not None else None
        text = self._render_conversation(pieces, trace_rendering=rendering)
        if not text:
            return []
        observations = [observation] + ([trace.observation] if trace and trace.observation else [])
        for acquired in observations:
            _collect_observation(acquired)
        trace_metadata = self._trace_metadata(trace=trace, rendering=rendering)
        unmet = self._unmet_trace_requirement(trace)
        if unmet is not None and trace is not None:
            return [
                self._build_trace_requirement_unmet_score(
                    scorable=scorable,
                    expectation=expectation,
                    reason=unmet,
                    trace=trace,
                    metadata=trace_metadata,
                    observations=observations,
                )
            ]
        child = self._get_wrapped_scorer()
        try:
            scores = await child._score_nested_async(
                scorable=ContentScorable(value=text),
                expectation=child._select_expectation(expectation=expectation),
            )
        except ScorerLLMResponseBlockedException as error:
            scores = [
                self._handle_blocked_judge_response(
                    error=error, objective=expectation.objective if expectation else None
                )
            ]
        results = []
        for child_score in scores:
            score = self._create_wrapper_score(child_score)
            score.scorable = scorable
            score.message_piece_id = None
            for acquired in observations:
                if acquired.id not in score.observation_ids:
                    score.observation_ids.append(acquired.id)
            if trace_metadata:
                score.score_metadata = {
                    **(score.score_metadata or {}),
                    **trace_metadata,
                    **self._citation_metadata(score=score, rendering=rendering),
                }
            results.append(score)
        return results

    async def _acquire_trace_evidence_async(self, *, conversation_id: str) -> _TraceEvidence:
        """
        Acquire execution evidence for every outbound request in the conversation.

        Returns:
            _TraceEvidence: The links, the acquired observation if any, and coverage gaps.

        Raises:
            ValueError: If the trace source returns evidence for a different scope or kind.
        """
        links = await resolve_conversation_trace_links_async(conversation_id=conversation_id, memory=self._memory)
        reasons = ["unlinked_requests"] if any(not link.linked for link in links) else []
        scope = trace_scope_from_links(links)
        if scope is None or self._trace_source is None:
            return _TraceEvidence(links=links, observation=None, reasons=(*reasons, "no_request_trace_links"))
        trace_source = self._trace_source
        key = ("trace", trace_source.get_identifier().hash, scope.trace_ids)
        observation = await _acquire_once_async(key=key, acquire=lambda: trace_source.acquire_async(scorable=scope))
        if (
            observation.scorable != scope
            or not isinstance(observation.payload, ExecutionTraceObservationPayload)
            or observation.payload.scope != scope
        ):
            raise ValueError("Trace source returned incompatible evidence or scope.")
        reasons.extend(observation.payload.coverage.reasons)
        return _TraceEvidence(links=links, observation=observation, reasons=tuple(dict.fromkeys(reasons)))

    def _unmet_trace_requirement(self, trace: _TraceEvidence | None) -> str | None:
        """
        Return why the configured trace requirement is not met, or None when it is.

        Returns:
            str | None: The unmet requirement's reason.
        """
        if trace is None or self._trace_requirement is TraceEvidenceRequirement.NONE:
            return None
        if self._trace_requirement is TraceEvidenceRequirement.COMPLETE and not trace.complete:
            return "trace_not_complete"
        if self._trace_requirement is TraceEvidenceRequirement.AVAILABLE and not trace.available:
            return "trace_unavailable"
        return None

    def _build_trace_requirement_unmet_score(
        self,
        *,
        scorable: ConversationScorable,
        expectation: ScoringExpectation | None,
        reason: str,
        trace: _TraceEvidence,
        metadata: dict[str, str | int | float],
        observations: list[Observation],
    ) -> Score:
        score = self._build_undetermined_score(
            rationale=(
                f"The judge was not called: this scorer requires {self._trace_requirement.value} execution "
                f"evidence, but the conversation's trace coverage is {trace.coverage_label} "
                f"({', '.join(trace.reasons) or 'no reasons reported'})."
            ),
            description="Required execution evidence is missing.",
            scorable=scorable,
            objective=expectation.objective if expectation else None,
            score_metadata={**metadata, self.EVIDENCE_REQUIREMENT_UNMET_KEY: reason},
        )
        score.observation_ids = [observation.id for observation in observations]
        return score

    def _trace_metadata(
        self, *, trace: _TraceEvidence | None, rendering: _TraceRendering | None
    ) -> dict[str, str | int | float]:
        if trace is None:
            return {}
        payload = trace.payload
        metadata: dict[str, str | int | float] = {
            "trace_coverage": trace.coverage_label,
            "trace_reasons": ",".join(trace.reasons),
            "trace_step_count": len(payload.steps) if payload else 0,
            "trace_requirement": self._trace_requirement.value,
        }
        if rendering is not None and rendering.truncated:
            metadata["trace_rendering_truncated"] = "true"
        return metadata

    def _citation_metadata(self, *, score: Score, rendering: _TraceRendering | None) -> dict[str, str]:
        """
        Record the execution steps the judge cited and any it cited that it was not shown.

        Returns:
            dict[str, str]: Citation metadata, empty when nothing was cited.
        """
        cited = sorted({int(index) for index in self._TRACE_CITATION.findall(score.score_rationale or "")})
        if not cited:
            return {}
        shown = rendering.rendered_step_indexes if rendering else frozenset()
        unknown = [index for index in cited if index not in shown]
        metadata = {"trace_cited_steps": json.dumps(cited)}
        if unknown:
            metadata["trace_citation_warnings"] = json.dumps([f"unknown_step:T{index}" for index in unknown])
        return metadata

    def _render_trace(self, trace: _TraceEvidence) -> _TraceRendering:
        """
        Render execution steps into one fenced block per linked request trace.

        Returns:
            _TraceRendering: The header, per-request blocks, and rendering statistics.
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
        budget = [self._max_rendered_trace_chars]
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
            body = body or self._NO_SPANS_LINE
            fence = f"EXECUTION TRACE {len(emitted)}"
            blocks[link.sequence] = f"[BEGIN {fence}]\n{body}[END {fence}]\n"
        return _TraceRendering(
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
        roots so no step is hidden. Content is dropped first when the budget runs low; then
        remaining steps are omitted.

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

    @staticmethod
    def _render_step(*, step: ExecutionStep, depth: int, include_content: bool) -> str:
        indent = "  " * (depth + 1)
        details = [f"status={step.status.value}"] if step.status is not TraceSpanStatus.UNSET else []
        details += [
            f"{label}={' '.join(value.split())}"
            for label, value in (("model", step.model), ("node", step.node_name))
            if value
        ]
        if step.input_tokens is not None or step.output_tokens is not None:
            details.append(f"tokens={step.input_tokens or 0}/{step.output_tokens or 0}")
        name = " ".join(step.name.split())
        lines = [f"{indent}T{step.index} {step.kind.value} {name}" + "".join(f" {d}" for d in details) + "\n"]
        for label, content in (
            ("error", step.error_content),
            ("input", step.input_content),
            ("output", step.output_content),
        ):
            if content is not None:
                lines.append(
                    ConversationScorer._render_content(
                        label=label, content=content, indent=indent, full=include_content
                    )
                )
        return "".join(lines)

    @staticmethod
    def _render_content(*, label: str, content: TraceContent, indent: str, full: bool) -> str:
        if not full or not content.text:
            reason = "not retained" if full else "omitted for length"
            return f"{indent}  {label}: ({content.original_length} chars {reason}; sha256={content.sha256[:16]})\n"
        note = f" (first {len(content.text)} of {content.original_length} chars)" if content.truncated else ""
        body = "".join(f"{indent}    {line}" for line in content.text.splitlines(keepends=True))
        return f"{indent}  {label}{note}:\n{body}\n"

    def _render_conversation(
        self, pieces: tuple[MessagePiece, ...], *, trace_rendering: _TraceRendering | None = None
    ) -> str:
        """
        Render each turn inside a numbered, role-labeled fence with indented content.

        The previous renderer emitted ``{role}: {text}`` with no fencing, so a
        target reply containing literal ``User:``/``Assistant:`` lines forged
        additional turns inside its own turn, rewriting the conversation the
        wrapped judge sees. Each turn is now bounded by ``[BEGIN {ROLE} TURN i]``
        / ``[END {ROLE} TURN i]`` delimiters and every content line is indented
        two spaces, so a forged role-prefix or fence closer inside stored
        content can never start at column 0. Content is preserved verbatim
        modulo the indent: ``splitlines(keepends=True)`` retains original line
        separators and trailing newlines while indenting every Unicode line-break
        variant. The extra newline before the closing fence belongs to the framing.

        Explicitly excluded data types are omitted before strict validation. They remain
        in the acquired conversation snapshot but are not presented to the wrapped scorer.

        With execution evidence, a coverage header comes first and each request's
        execution block follows the turns that answered it. Trace content is indented the
        same way, so it cannot forge fences either.

        Args:
            pieces (tuple[MessagePiece, ...]): The conversation pieces to render.
            trace_rendering (_TraceRendering | None): Rendered execution evidence, if any.

        Returns:
            str: The fenced transcript, or an empty string when no turn is applicable.

        Raises:
            ValueError: If strict validation rejects a role-supported conversation piece.
        """
        lines = []
        turn_index = 0
        blocks = trace_rendering.blocks if trace_rendering else {}
        request_sequences = trace_rendering.request_sequences if trace_rendering else frozenset()
        pending: str | None = None
        pending_sequence: int | None = None
        for piece in pieces:
            if piece.sequence in request_sequences and piece.sequence != pending_sequence:
                if pending:
                    lines.append(pending)
                pending, pending_sequence = blocks.get(piece.sequence), piece.sequence
            if piece.api_role not in ("user", "assistant", "tool") or not self._validator.is_role_supported(piece):
                continue
            if piece.converted_value_data_type in self._excluded_data_types:
                continue
            if self._validator._enforce_all_pieces_valid and not self._validator.is_message_piece_supported(piece):
                raise ValueError(
                    f"Conversation piece {piece.id} with data type {piece.converted_value_data_type} "
                    "is not supported by this conversation scorer."
                )
            role = piece.api_role.capitalize()
            if piece.is_simulated:
                role += " (simulated)"
            partial = piece.prompt_metadata.get("partial_content")
            text = (
                str(partial)
                if self.should_score_blocked_content and piece.is_blocked() and partial
                else piece.converted_value
            )
            fence = f"{role.upper()} TURN {turn_index}"
            body = "".join("  " + line for line in text.splitlines(keepends=True))
            lines.append(f"[BEGIN {fence}]\n{body}\n[END {fence}]\n")
            turn_index += 1
        if not turn_index:
            return ""
        if pending:
            lines.append(pending)
        if trace_rendering is None:
            return "".join(lines)
        return trace_rendering.header + "".join(lines)

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        """
        Not used - ConversationScorer operates at conversation level via
        ``_score_prepared_message_async``.

        This implementation satisfies the Scorer ABC requirement but is never called
        since ConversationScorer overrides ``_score_prepared_message_async``.
        """
        raise NotImplementedError("ConversationScorer does not support piecewise scoring")

    @abstractmethod
    def _get_wrapped_scorer(self) -> Scorer:
        """
        Abstract method to enforce that ConversationScorer cannot be instantiated directly.

        This must be implemented by the factory-created subclass.
        """

    def validate_return_scores(self, scores: list[Score]) -> None:
        """
        Validate scores by delegating to the wrapped scorer's validation.

        Args:
            scores (list[Score]): The scores to validate.
        """
        wrapped_scorer = self._get_wrapped_scorer()
        wrapped_scorer.validate_return_scores(scores)


def create_conversation_scorer(
    *,
    scorer: Scorer,
    validator: ScorerPromptValidator | None = None,
    source: ObservationSource[ConversationScorable] | None = None,
    excluded_data_types: Sequence[PromptDataType] = (),
    trace_source: ObservationSource[TraceScorable] | None = None,
    trace_requirement: TraceEvidenceRequirement = TraceEvidenceRequirement.NONE,
    max_rendered_trace_chars: int = 60000,
) -> Scorer:
    """
    Create a ConversationScorer that inherits from the same type as the wrapped scorer.

    This factory dynamically creates a ConversationScorer class that inherits from the
    wrapped scorer's message-family base. The returned scorer is an instance of both
    ``ConversationScorer`` and the wrapped scorer's result family.

    Args:
        scorer (Scorer): The true/false or float-scale scorer to wrap for
            conversation-level evaluation. It must support text ``ContentScorable`` evidence.
        validator (ScorerPromptValidator | None): Optional validator override.
            If not provided, uses the conversation scorer's default text validator.
        source (ObservationSource[ConversationScorable] | None): Whole-conversation acquisition source.
        excluded_data_types (Sequence[PromptDataType]): Data types to omit from the judged
            transcript, even under strict validation. The retained snapshot is unchanged.
            Defaults to no exclusions.
        trace_source (ObservationSource[TraceScorable] | None): Execution evidence source. When
            set, the traces linked to the conversation's outbound requests are acquired, saved
            as an observation, and rendered after the turns they produced, with step IDs such as
            ``[T3]`` that the judge can cite. Defaults to None.
        trace_requirement (TraceEvidenceRequirement): Evidence required before the judge is
            called. An unmet requirement returns an undetermined score marked with
            ``ConversationScorer.EVIDENCE_REQUIREMENT_UNMET_KEY``, without a judge call. Defaults to NONE.
        max_rendered_trace_chars (int): Character budget for rendered execution steps.
            Content is dropped first, then steps. Defaults to 60000.

    Returns:
        Scorer: A ConversationScorer instance that is also an instance of the wrapped scorer's type.

    Raises:
        TypeError: If the dynamic scorer does not inherit from ``Scorer``.
        ValueError: If the scorer is outside the true/false and float-scale families, a trace
            requirement has no trace source, or the rendering budget is not positive.

    Example:
        >>> float_scorer = SelfAskLikertScorer.from_likert_scale(chat_target=target, likert_scale=scale)
        >>> conversation_scorer = create_conversation_scorer(scorer=float_scorer)
        >>> isinstance(conversation_scorer, FloatScaleScorer)  # True
        >>> isinstance(conversation_scorer, ConversationScorer)  # True
    """
    requirement = TraceEvidenceRequirement(trace_requirement)
    if trace_source is None and requirement is not TraceEvidenceRequirement.NONE:
        raise ValueError("A trace requirement needs a trace_source to acquire execution evidence.")
    if max_rendered_trace_chars < 1:
        raise ValueError("max_rendered_trace_chars must be positive.")
    # Determine the base class of the wrapped scorer
    scorer_base_class: type[Scorer] | None = None

    if isinstance(scorer, FloatScaleScorer):
        scorer_base_class = MessageFloatScaleScorer
    elif isinstance(scorer, TrueFalseScorer):
        scorer_base_class = MessageTrueFalseScorer
    else:
        raise ValueError(
            f"Unsupported scorer type: {type(scorer).__name__}. "
            "Scorer must belong to the true/false or float-scale family."
        )

    # Dynamically create a class that inherits from both ConversationScorer and the scorer's base class
    class DynamicConversationScorer(ConversationScorer, scorer_base_class):  # type: ignore[valid-type]  # type: ignore[ty:unsupported-base]
        """Dynamic ConversationScorer that inherits from both ConversationScorer and the wrapped scorer's base class."""

        _wrapped_scorer: Scorer

        def __init__(self) -> None:
            # Initialize with the validator and wrapped scorer
            MessageScorer.__init__(self, validator=validator or ConversationScorer._DEFAULT_VALIDATOR)
            self._wrapped_scorer = scorer
            self._source = source if source is not None else ConversationSource()
            self._excluded_data_types = tuple(sorted(set(excluded_data_types)))
            self._trace_source = trace_source
            self._trace_requirement = requirement
            self._max_rendered_trace_chars = max_rendered_trace_chars

        def _get_wrapped_scorer(self) -> Scorer:
            """
            Return the wrapped scorer.

            Returns:
                Scorer: The scorer used for conversation-level evaluation.
            """
            return self._wrapped_scorer

        def _build_identifier(self) -> ComponentIdentifier:
            """
            Build the scorer evaluation identifier for this conversation scorer.

            Returns:
                ComponentIdentifier: The identifier for this scorer.

            Raises:
                TypeError: If identifier construction returns an unexpected type.
            """
            identifier = self._create_identifier(
                params={
                    "rendering_version": 2,
                    "supported_roles": self._validator._supported_roles,
                    "should_score_blocked_content": self.should_score_blocked_content,
                    **({"excluded_data_types": self._excluded_data_types} if self._excluded_data_types else {}),
                    **(
                        {
                            "enforce_all_pieces_valid": True,
                            "supported_data_types": self._validator._supported_data_types,
                            "required_metadata": self._validator._required_metadata,
                            "max_text_length": self._validator._max_text_length,
                        }
                        if self._validator._enforce_all_pieces_valid
                        else {}
                    ),
                    **(
                        {
                            "trace_rendering_version": self.TRACE_RENDERING_VERSION,
                            "trace_requirement": self._trace_requirement.value,
                            "max_rendered_trace_chars": self._max_rendered_trace_chars,
                        }
                        if self._trace_source is not None
                        else {}
                    ),
                },
                sub_scorers=[self._wrapped_scorer.get_identifier()],
                children={
                    "source": self._source.get_identifier(),
                    **({"trace_source": self._trace_source.get_identifier()} if self._trace_source is not None else {}),
                },
            )
            if not isinstance(identifier, ComponentIdentifier):
                raise TypeError("Conversation scorer identifier must be a ComponentIdentifier")
            return identifier

    conversation_scorer = DynamicConversationScorer()
    if not isinstance(conversation_scorer, Scorer):
        raise TypeError("Dynamic conversation scorer must inherit from Scorer")
    return conversation_scorer
