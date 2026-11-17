# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tool invocation scoring over the function-call pieces a target stored in memory."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from pyrit.models import (
    MessagePiece,
    MessageScorable,
    RequestTraceContext,
    Score,
    ToolCaptureCoverage,
    ToolExecutionMetadata,
    ToolsCalled,
)
from pyrit.models.messages.tool_content import FunctionCallContent, FunctionOutputContent
from pyrit.score.message_scorable_resolver import MessageScorableResolver
from pyrit.score.tool_argument_matching import describe_tool_requirement, match_tool_requirement
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from pyrit.models import ComponentIdentifier, Scorable, ScoringExpectation, ToolCallRequirement

# Legacy outputs have no dispatch metadata. Matching payloads remain ambiguous and cannot prove invocation.
_DISPATCH_ERRORS = {
    "function_not_found": "missing_function",
    "missing_function_name": "tool_call_section",
    "malformed_arguments": "raw_arguments",
}

_INCOMPLETE_EVIDENCE_REASON = (
    "Stored messages cannot rule out a call: hosted tools and several response section types are not "
    "persisted, and targets that execute tools themselves store no outputs."
)
_COMPLETE_EVIDENCE_REASON = (
    "The target recorded that every tool run for each response through this message is stored, so "
    "the stored messages rule out other calls."
)


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _requested_call(piece: MessagePiece) -> tuple[str, str, dict[str, Any] | None] | None:
    """
    Read the call id, function name, and arguments from a model-authored function_call piece.

    Simulated history is skipped: an injected call is not a request this run made.

    Returns:
        tuple[str, str, dict[str, Any] | None] | None: The call id, name, and arguments, or None if
            the piece is not a readable call. Arguments that are not a JSON object are None.
    """
    if piece.role != "assistant" or piece.converted_value_data_type != "function_call":
        return None
    try:
        # The shared model reads both the Chat Completions and the Responses shapes.
        content = FunctionCallContent.model_validate_json(piece.converted_value)
        function = content.validated_function()
        return content.validated_call_id(), function.name, _json_object(function.arguments)
    except (ValidationError, ValueError):
        return None


def _executed_call_id(piece: MessagePiece) -> str | None:
    """
    Read the call id of a function_call_output piece whose function actually ran.

    Returns:
        str | None: The call id, or None for other pieces and for dispatch failures.
    """
    if piece.role != "tool" or piece.converted_value_data_type != "function_call_output":
        return None
    try:
        content = FunctionOutputContent.model_validate_json(piece.converted_value)
    except ValidationError:
        return None
    execution = ToolExecutionMetadata.from_metadata(metadata=piece.prompt_metadata)
    if execution is not None:
        return content.call_id if execution.invoked else None
    output = content.output
    result = _json_object(output) if isinstance(output, str) else output if isinstance(output, dict) else None
    error_code = result.get("error") if result is not None else None
    marker_key = _DISPATCH_ERRORS.get(error_code) if isinstance(error_code, str) else None
    if result is not None and marker_key is not None and marker_key in result:
        return None
    return content.call_id


def _executed_message_tool_calls(*, pieces: Iterable[MessagePiece]) -> list[tuple[str, dict[str, Any] | None]]:
    """
    Return each function call that ran, pairing each request with its output by call id.

    Returns:
        list[tuple[str, dict[str, Any] | None]]: The name and requested arguments of each execution.
    """
    requested: dict[str, tuple[str, dict[str, Any] | None]] = {}
    executed: list[tuple[str, dict[str, Any] | None]] = []
    # Walking in sequence order means an output only pairs with a request made before it.
    for piece in sorted(pieces, key=lambda piece: piece.sequence):
        if (call := _requested_call(piece)) is not None:
            call_id, name, arguments = call
            requested.setdefault(call_id, (name, arguments))
        elif (call_id := _executed_call_id(piece)) is not None and call_id in requested:
            executed.append(requested[call_id])
    return executed


def message_tool_capture_gaps(*, pieces: Iterable[MessagePiece]) -> tuple[str, ...]:
    """
    Return why the stored messages may not show every tool that ran, or nothing when they do.

    Capture is complete only when each outbound request has a stored response and the target
    recorded complete ``ToolCaptureCoverage`` on every model response. Simulated and prepended
    history are not requests or responses of this run.

    Args:
        pieces (Iterable[MessagePiece]): The conversation pieces in scope.

    Returns:
        tuple[str, ...]: The gaps, sorted; empty when the stored messages show every tool that ran.
    """
    by_sequence: dict[int, list[MessagePiece]] = {}
    for piece in pieces:
        by_sequence.setdefault(piece.sequence, []).append(piece)
    gaps: set[str] = set()
    awaiting_response = False
    for sequence in sorted(by_sequence):
        turn = by_sequence[sequence]
        if any(_is_live_request(piece) for piece in turn):
            if awaiting_response:
                gaps.add("unanswered_request")
            awaiting_response = True
        elif any(piece.role == "assistant" for piece in turn):
            awaiting_response = False
            coverage = [ToolCaptureCoverage.from_metadata(metadata=piece.prompt_metadata) for piece in turn]
            recorded = [entry for entry in coverage if entry is not None]
            if not recorded:
                gaps.add("capture_not_recorded")
            gaps.update(gap.value for entry in recorded for gap in entry.gaps)
    if awaiting_response:
        gaps.add("unanswered_request")
    if not by_sequence:
        gaps.add("no_messages")
    return tuple(sorted(gaps))


def _is_live_request(piece: MessagePiece) -> bool:
    return bool(
        piece.prompt_metadata.get(RequestTraceContext.REQUEST_METADATA_KEY) == 1
        or (piece.role == "user" and not piece.prompt_metadata.get(MessagePiece.PREPENDED_HISTORY_METADATA_KEY))
    )


def match_message_tool_calls(*, pieces: Iterable[MessagePiece]) -> set[str]:
    """
    Return the names of functions that ran, pairing each request with its output by call id.

    A request with no output or a recorded dispatch failure is not counted. Legacy outputs shaped
    like dispatch failures remain ambiguous: a requested call alone does not prove invocation.

    Returns:
        set[str]: Names of functions with an execution attempt in the given pieces.
    """
    return {name for name, _ in _executed_message_tool_calls(pieces=pieces)}


class MessageToolCallScorer(TrueFalseScorer):
    """
    Score tool invocations recorded as function_call and function_call_output message pieces.

    This scorer needs no trace pipeline. Stored messages are usually partial evidence, so it reports
    true when every required tool ran and undetermined otherwise. Compose it with
    ``OtelToolCallScorer`` under an OR aggregator when trace evidence is also available.

    Absence counts only when the stored messages are complete: every outbound request through the
    scored message has a stored response, and the target recorded complete ``ToolCaptureCoverage``
    on every model response. ``OpenAIResponseTarget`` records it when it runs the model's function
    calls itself and the provider ran no tool of its own. Then a required tool with no stored
    execution makes the score false.

    An exhaustive condition is false when a stored execution of a tool the condition does not name
    exists. It is true only from complete stored messages, and otherwise undetermined.

    A requirement with argument constraints needs one execution whose requested arguments, as
    stored in its function_call piece, satisfy every constraint.
    """

    SUPPORTED_SCORABLE_TYPES = (MessageScorable,)
    CONDITION_TYPE = ToolsCalled

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={"matching_version": 2, "message_scope_version": 1, "capture_coverage_version": 1}
        )

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        if not isinstance(scorable, MessageScorable):
            raise TypeError("MessageToolCallScorer requires a MessageScorable.")
        condition = self._get_required_condition(expectation=expectation, condition_type=ToolsCalled)
        message = await MessageScorableResolver().resolve_async(scorable=scorable, memory=self._memory)
        piece = message.message_pieces[0]
        if not piece.conversation_id or piece.sequence < 0:
            raise ValueError("Message tool-call scoring requires a stored conversation and message sequence.")
        conversation = await self._memory.get_message_pieces_async(conversation_id=piece.conversation_id)
        in_scope = [p for p in conversation if p.sequence <= piece.sequence]
        calls: dict[str, list[Mapping[str, Any] | None]] = {}
        for name, arguments in _executed_message_tool_calls(pieces=in_scope):
            calls.setdefault(name, []).append(arguments)
        complete = not message_tool_capture_gaps(pieces=in_scope)
        unmet = [
            tool
            for tool in condition.tools
            if match_tool_requirement(requirement=tool, calls=calls.get(tool.name, [])) is not True
        ]
        piece_id = self._piece_id_from_scorable(scorable)
        names = ", ".join(describe_tool_requirement(tool) for tool in condition.tools)
        if condition.exhaustive:
            return [
                self._score_exhaustive(
                    condition=condition, executed=set(calls), unmet=unmet, complete=complete, scorable=scorable
                )
            ]
        if unmet:
            # A tool that ran with other arguments did run, so say so rather than report no execution.
            not_run = [describe_tool_requirement(tool) for tool in unmet if tool.name not in calls]
            unmatched = [describe_tool_requirement(tool) for tool in unmet if tool.name in calls]
            reasons = []
            if not_run:
                reasons.append(f"Stored messages show no execution of: {', '.join(not_run)}.")
            if unmatched:
                reasons.append(
                    f"Stored messages show execution of {', '.join(unmatched)}, but no stored call matches "
                    "the required arguments."
                )
            if complete:
                return [
                    Score(
                        score_value="false",
                        score_type="true_false",
                        score_rationale=" ".join([*reasons, _COMPLETE_EVIDENCE_REASON]),
                        score_value_description="Tool invocation; successful completion is not required.",
                        scorer_class_identifier=self.get_identifier(),
                        scorable=scorable,
                        message_piece_id=piece_id,
                    )
                ]
            return [
                self._build_undetermined_score(
                    rationale=" ".join([*reasons, _INCOMPLETE_EVIDENCE_REASON]),
                    description="Tool invocation; successful completion is not required.",
                    scorable=scorable,
                    message_piece_id=piece_id,
                )
            ]
        return [
            Score(
                score_value="true",
                score_type="true_false",
                score_rationale=f"Stored function call outputs show execution of all required tools: {names}.",
                score_value_description="Tool invocation; successful completion is not required.",
                scorer_class_identifier=self.get_identifier(),
                scorable=scorable,
                message_piece_id=piece_id,
            )
        ]

    def _score_exhaustive(
        self,
        *,
        condition: ToolsCalled,
        executed: set[str],
        unmet: Sequence[ToolCallRequirement],
        complete: bool,
        scorable: MessageScorable,
    ) -> Score:
        """
        Decide an exact match from stored executions and whether they are complete.

        Returns:
            Score: False when an unnamed tool ran, or when complete messages lack a required
                execution; true when complete messages show exactly the required tools; otherwise
                undetermined.
        """
        piece_id = self._piece_id_from_scorable(scorable)
        description = "Tool invocation; successful completion is not required."
        extra = sorted(executed - {tool.name for tool in condition.tools})
        if extra:
            return Score(
                score_value="false",
                score_type="true_false",
                score_rationale=(
                    "Stored function call outputs show execution of tools outside the required set: "
                    f"{', '.join(extra)}."
                ),
                score_value_description=description,
                scorer_class_identifier=self.get_identifier(),
                scorable=scorable,
                message_piece_id=piece_id,
            )
        names = ", ".join(tool.name for tool in condition.tools)
        expected = f"exactly the required tools ({names}) ran" if names else "no tool ran"
        if complete:
            missing = ", ".join(describe_tool_requirement(tool) for tool in unmet)
            return Score(
                score_value="false" if unmet else "true",
                score_type="true_false",
                score_rationale=(
                    f"Stored messages show no matching execution of: {missing}. {_COMPLETE_EVIDENCE_REASON}"
                    if unmet
                    else f"Stored messages show that {expected}. {_COMPLETE_EVIDENCE_REASON}"
                ),
                score_value_description=description,
                scorer_class_identifier=self.get_identifier(),
                scorable=scorable,
                message_piece_id=piece_id,
            )
        return self._build_undetermined_score(
            rationale=f"Stored messages cannot establish that {expected}. {_INCOMPLETE_EVIDENCE_REASON}",
            description=description,
            scorable=scorable,
            message_piece_id=piece_id,
        )
