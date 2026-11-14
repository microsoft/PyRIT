# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tool invocation scoring over the function-call pieces a target stored in memory."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from pyrit.models import MessageScorable, Score, ToolExecutionMetadata, ToolsCalled
from pyrit.models.messages.tool_content import FunctionCallContent, FunctionOutputContent
from pyrit.score.message_scorable_resolver import MessageScorableResolver
from pyrit.score.tool_argument_matching import describe_tool_requirement, match_tool_requirement
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pyrit.models import ComponentIdentifier, MessagePiece, Scorable, ScoringExpectation

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

    This scorer needs no trace pipeline. Stored messages are partial evidence, so it reports true
    when every required tool ran and undetermined otherwise; it never reports false. Compose it
    with ``OtelToolCallScorer`` under an OR aggregator when trace evidence is also available.

    A requirement with argument constraints needs one execution whose requested arguments, as
    stored in its function_call piece, satisfy every constraint.
    """

    SUPPORTED_SCORABLE_TYPES = (MessageScorable,)
    CONDITION_TYPE = ToolsCalled

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(params={"matching_version": 2, "message_scope_version": 1})

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        if not isinstance(scorable, MessageScorable):
            raise TypeError("MessageToolCallScorer requires a MessageScorable.")
        condition = self._get_required_condition(expectation=expectation, condition_type=ToolsCalled)
        message = await MessageScorableResolver().resolve_async(scorable=scorable, memory=self._memory)
        piece = message.message_pieces[0]
        if not piece.conversation_id or piece.sequence < 0:
            raise ValueError("Message tool-call scoring requires a stored conversation and message sequence.")
        conversation = await self._memory.get_message_pieces_async(conversation_id=piece.conversation_id)
        calls: dict[str, list[Mapping[str, Any] | None]] = {}
        for name, arguments in _executed_message_tool_calls(
            pieces=(p for p in conversation if p.sequence <= piece.sequence)
        ):
            calls.setdefault(name, []).append(arguments)
        unmet = [
            tool
            for tool in condition.tools
            if match_tool_requirement(requirement=tool, calls=calls.get(tool.name, [])) is not True
        ]
        piece_id = self._piece_id_from_scorable(scorable)
        names = ", ".join(describe_tool_requirement(tool) for tool in condition.tools)
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
