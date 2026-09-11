# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import hashlib
import json
import uuid
from string import Formatter
from typing import TYPE_CHECKING, cast

from pyrit.exceptions import (
    EmptyResponseException,
    InvalidJsonException,
    ScorerLLMResponseBlockedException,
)
from pyrit.models import (
    Acquisition,
    LlmJudgmentObservationPayload,
    Message,
    MessagePiece,
    MessageScorable,
    Observation,
    ScorableUnion,
    ScoringExpectation,
    scoring_expectation_fingerprint,
)
from pyrit.models.score.scorable import SCORABLE_TYPES
from pyrit.prompt_normalizer import PromptNormalizer, send_json_with_retry_async
from pyrit.score.observation import (
    NonReplayableObservationError,
    _collect_observation,
    _get_current_scorable,
    _get_current_scored_message_piece,
    _get_current_scoring_expectation,
    _has_observation_collection,
    _ObservationEvidence,
    _replay_message_piece_id,
    _response_piece_digest,
    _scored_evidence_digest,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.models import (
        ComponentIdentifier,
        PromptDataType,
        UnvalidatedScore,
    )
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.response_handler import ResponseHandler


def _format_string_references_message_piece(template: str | None) -> bool:
    """
    Check whether a format string reads the complete message piece.

    Returns:
        bool: True when ``message_piece`` is a referenced format field.
    """
    if not template:
        return False
    for _, field_name, format_spec, _ in Formatter().parse(template):
        if field_name:
            root_name = field_name.split(".", maxsplit=1)[0].split("[", maxsplit=1)[0]
            if root_name == "message_piece":
                return True
        if format_spec and _format_string_references_message_piece(format_spec):
            return True
    return False


async def _run_llm_scoring_async(
    *,
    chat_target: PromptTarget,
    system_prompt: str | None,
    response_handler: ResponseHandler,
    value: str,
    data_type: PromptDataType,
    scored_prompt_id: str | uuid.UUID,
    scorer_identifier: ComponentIdentifier,
    prepended_text: str | None = None,
    category: Sequence[str] | str | None = None,
    objective: str | None = None,
    normalizer: PromptNormalizer | None = None,
    observation_metadata: Mapping[str, str] | None = None,
    requires_message_piece_evidence: bool = False,
) -> UnvalidatedScore:
    """
    Perform a single scoring round-trip against an LLM target and delegate parsing.

    This is the shared LLM evaluation mechanism: it optionally sets a system prompt on the target, sends
    the value to be scored (forwarding ``response_handler.json_response_config`` so targets that
    support structured output can enforce it), and delegates parsing and validation to
    ``response_handler``. The round-trip is routed through a ``PromptNormalizer`` via
    ``send_json_with_retry_async`` so the scorer's question and the target's answer are persisted
    to memory (a full audit trail, and a real conversation an attack can link as a SCORE-type
    related conversation) and so JSON retries roll memory back to a clean baseline between attempts
    instead of replaying the target's own malformed reply. It is intentionally stateless and
    independent of any particular ``Scorer`` so that scorers can compose it without inheriting LLM
    machinery.

    The round-trip owns only the transport; the ``ResponseHandler`` owns the response contract —
    the optional response schema and turning raw text into a validated ``UnvalidatedScore``.

    This function is intentionally module-internal (underscore-prefixed): it is a composition
    primitive with no public-API stability or deprecation contract. Scorers in this package call
    it directly; external callers should compose scorers rather than this helper.

    Args:
        chat_target (PromptTarget): The target LLM to send the message to.
        system_prompt (str | None): The system-level prompt that guides the target LLM. When None,
            the request is sent without configuring a system prompt.
        response_handler (ResponseHandler): Owns the response contract: supplies the optional
            response schema and turns the target's raw text into an ``UnvalidatedScore``.
        value (str): The content to be scored (e.g. text, image path, audio path).
        data_type (PromptDataType): The data type of ``value`` (e.g. "text", "image_path").
        scored_prompt_id (str | uuid.UUID): The ID of the message piece being scored.
        scorer_identifier (ComponentIdentifier): Identifier of the calling scorer, stored on
            the resulting score.
        prepended_text (str | None): Text context to prepend before ``value`` as a separate
            piece. Useful for adding objective/context when scoring non-text content.
            Defaults to None.
        category (Sequence[str] | str | None): The category of the score. May instead be parsed
            from the response; supplying both is an error. Defaults to None.
        objective (str | None): Transitional objective context for direct helper callers.
            Defaults to None.
        normalizer (PromptNormalizer | None): Normalizer used to send the scoring round-trip
            and whose memory is rolled back between JSON retries. Injectable for testing;
            defaults to a fresh ``PromptNormalizer()`` when not supplied.
        observation_metadata (Mapping[str, str] | None): Scorer-specific state required to
            reconstruct the response parser during replay. Defaults to None.
        requires_message_piece_evidence (bool): Whether the rendered request reads fields that a
            content-only observation cannot retain. Defaults to False.

    Returns:
        UnvalidatedScore: The parsed score, whose ``raw_score_value`` still needs to be
            normalized and validated by the caller.

    Raises:
        ScorerLLMResponseBlockedException: If the scorer's LLM response is blocked by
            content filtering. The transport only surfaces the condition; the calling
            ``Scorer`` owns the policy for whether to raise or return a default score.
        EmptyResponseException: If the scorer's LLM response has message pieces but none of them
            are text and none are blocked (a rare no-text-modality shape). Note a genuinely empty
            text reply does NOT surface here: the normalizer converts an empty target response into
            an empty text piece, which fails JSON parsing and is therefore retried and, if still
            empty, ultimately raised as ``InvalidJsonException``.
        InvalidJsonException: If the response is not valid JSON, is missing required keys, or
            fails the handler's value validation. This also covers an empty text reply (normalized
            to ``""``), which is retried before being surfaced here.
        TypeError: If the active scorable cannot be persisted on an observation.
        RuntimeError: If the transport returns no terminal response.
        Exception: For other unexpected errors during scoring.
    """
    conversation_id = str(uuid.uuid4())
    expectation = _get_current_scoring_expectation()
    if expectation is None and objective is not None:
        expectation = ScoringExpectation(objective=objective)
    expectation_fingerprint = scoring_expectation_fingerprint(expectation or ScoringExpectation())
    replay_contract_fingerprint = _replay_contract_fingerprint(
        response_handler=response_handler,
        category=category,
    )
    active_scorable = _get_current_scorable()
    if active_scorable is not None and not isinstance(active_scorable, SCORABLE_TYPES):
        raise TypeError(f"{type(active_scorable).__name__} cannot anchor an LLM observation.")
    observation_scorable = cast("ScorableUnion | None", active_scorable)
    resolved_normalizer = normalizer or PromptNormalizer()
    scored_piece_id = uuid.UUID(str(scored_prompt_id)) if observation_scorable is not None else None
    scored_message_piece = (
        _get_current_scored_message_piece(scored_piece_id=cast("uuid.UUID", scored_piece_id))
        if isinstance(observation_scorable, MessageScorable)
        else None
    )
    scored_evidence_digest = (
        _scored_evidence_digest(
            scorable=observation_scorable,
            scored_piece_id=cast("uuid.UUID", scored_piece_id),
            memory=resolved_normalizer.memory,
            scored_message_piece=scored_message_piece,
        )
        if observation_scorable is not None
        and (not isinstance(observation_scorable, MessageScorable) or scored_message_piece is not None)
        else None
    )
    has_required_evidence = not requires_message_piece_evidence or isinstance(observation_scorable, MessageScorable)
    can_collect_observation = (
        observation_scorable is not None and scored_evidence_digest is not None and has_required_evidence
    )

    if system_prompt is not None:
        chat_target.set_system_prompt(
            system_prompt=system_prompt,
            conversation_id=conversation_id,
        )
    # Forward the JSON-response request (format and any schema together) via the handler's
    # canonical config; the target's normalization pipeline omits the schema when it cannot
    # natively enforce one.
    prompt_metadata = response_handler.json_response_config.to_metadata()

    # Build message pieces - prepended text context first (if provided), then the main message being scored
    message_pieces: list[MessagePiece] = []

    # Add prepended text context piece if provided (e.g., objective context for non-text scoring)
    if prepended_text:
        message_pieces.append(
            MessagePiece(
                role="user",
                original_value=prepended_text,
                original_value_data_type="text",
                converted_value_data_type="text",
                conversation_id=conversation_id,
                prompt_metadata=prompt_metadata,
            )
        )

    # Add the main message piece being scored
    message_pieces.append(
        MessagePiece(
            role="user",
            original_value=value,
            original_value_data_type=data_type,
            converted_value_data_type=data_type,
            conversation_id=conversation_id,
            prompt_metadata=prompt_metadata,
        )
    )

    scorer_llm_request = Message(message_pieces=message_pieces)

    # Resolve the text piece that holds the JSON response (score_value + rationale). The normalizer
    # converts an empty or blocked-then-empty target response into an empty text piece, so a genuine
    # empty reply lands here as text_piece.converted_value == "" -> parse fails -> retried as invalid
    # JSON. The text_piece-is-None branch below therefore only fires for a response that has pieces
    # but no text piece: a content-filter block surfaces as its own exception (the calling Scorer
    # owns whether to raise or fall back), and any other no-text shape is a genuine empty/malformed
    # error. Neither of those is retried; only invalid JSON triggers a retry.
    terminal_response: Message | None = None

    def _capture_response(response: Message) -> None:
        nonlocal terminal_response
        terminal_response = response

    def _parse(response: Message) -> UnvalidatedScore:
        text_piece = next(
            (piece for piece in response.message_pieces if piece.converted_value_data_type == "text"), None
        )
        if text_piece is None:
            if any(piece.is_blocked() for piece in response.message_pieces):
                raise ScorerLLMResponseBlockedException(
                    message=(
                        f"The scorer's LLM response was blocked by content filtering while scoring "
                        f"prompt ID: {scored_prompt_id}. Consider using a scorer endpoint with "
                        f"content filtering disabled for red-teaming workflows."
                    )
                )
            raise EmptyResponseException(
                message=(
                    f"The scorer's LLM response contained no text to parse while scoring prompt ID: {scored_prompt_id}."
                )
            )

        return response_handler.parse(
            response_text=text_piece.converted_value,
            scorer_identifier=scorer_identifier,
            scored_prompt_id=scored_prompt_id,
            category=category,
            objective=expectation.objective if expectation else None,
        )

    # Route the round-trip through the normalizer so the scorer Q&A is persisted and JSON retries
    # replay on a clean history.
    try:
        unvalidated_score = await send_json_with_retry_async(
            normalizer=resolved_normalizer,
            target=chat_target,
            message=scorer_llm_request,
            conversation_id=conversation_id,
            parse=_parse,
            on_response=_capture_response,
        )
    except ScorerLLMResponseBlockedException as error:
        if terminal_response is not None and can_collect_observation and _has_observation_collection():
            observation = _build_llm_observation(
                acquisition=Acquisition.ERROR,
                response=terminal_response,
                scorable=observation_scorable,
                scorer_identifier=scorer_identifier,
                scored_piece_id=cast("uuid.UUID", scored_piece_id),
                scored_evidence_digest=scored_evidence_digest,
                expectation_fingerprint=expectation_fingerprint,
                replay_contract_fingerprint=replay_contract_fingerprint,
                metadata={
                    **dict(observation_metadata or {}),
                    "reason": "scorer_response_blocked",
                },
            )
            _collect_observation(observation)
            error.observation_id = observation.id
        raise
    except (EmptyResponseException, InvalidJsonException):
        # Terminal / caller-owned outcomes: propagate unchanged so the calling Scorer can apply
        # its own policy (fall back, raise, or -- for invalid JSON -- surface the retry exhaustion).
        raise
    except Exception as ex:
        raise Exception(f"Error scoring prompt with original prompt ID: {scored_prompt_id}") from ex

    if terminal_response is None:
        raise RuntimeError("The LLM scoring transport returned no terminal response.")
    unvalidated_score.scored_expectation = expectation
    unvalidated_score.objective = expectation.objective if expectation else None
    if can_collect_observation and _has_observation_collection():
        observation = _build_llm_observation(
            acquisition=Acquisition.COMPLETE,
            response=terminal_response,
            scorable=observation_scorable,
            scorer_identifier=scorer_identifier,
            scored_piece_id=cast("uuid.UUID", scored_piece_id),
            scored_evidence_digest=scored_evidence_digest,
            expectation_fingerprint=expectation_fingerprint,
            replay_contract_fingerprint=replay_contract_fingerprint,
            metadata=dict(observation_metadata or {}),
        )
        _collect_observation(observation)
        unvalidated_score.scorable = observation_scorable
        unvalidated_score.observation_ids.append(observation.id)
    return unvalidated_score


def _build_llm_observation(
    *,
    acquisition: Acquisition,
    response: Message,
    scorable: ScorableUnion,
    scorer_identifier: ComponentIdentifier,
    scored_piece_id: uuid.UUID,
    scored_evidence_digest: str,
    expectation_fingerprint: str,
    replay_contract_fingerprint: str | None,
    metadata: dict[str, str] | None = None,
) -> Observation:
    """
    Build an expectation-bound observation over one retained LLM response.

    Returns:
        Observation: The managed judgment evidence.
    """
    return Observation(
        source_identifier=scorer_identifier,
        acquisition=acquisition,
        scorable=scorable,
        payload=LlmJudgmentObservationPayload(
            scored_piece_id=scored_piece_id,
            message_piece_ids=tuple(piece.id for piece in response.message_pieces),
            message_piece_digests=tuple(
                _response_piece_digest(piece, include_id=True) for piece in response.message_pieces
            ),
            scored_evidence_digest=scored_evidence_digest,
            expectation_fingerprint=expectation_fingerprint,
            replay_contract_fingerprint=replay_contract_fingerprint,
        ),
        metadata=metadata or {},
    )


def _validate_llm_replay_compatibility(
    *,
    observation: Observation,
    expectation: ScoringExpectation | None,
    scorer_identifier: ComponentIdentifier,
) -> None:
    """
    Require the original scorer and expectation for retained LLM judgments.

    Raises:
        NonReplayableObservationError: If the scorer or expectation changed.
    """
    if (
        observation.source_identifier.hash != scorer_identifier.hash
        or observation.source_identifier.pyrit_version != scorer_identifier.pyrit_version
    ):
        raise NonReplayableObservationError(
            "An LLM judgment can only replay with the scorer configuration that acquired it."
        )
    if observation.payload.expectation_fingerprint != scoring_expectation_fingerprint(
        expectation or ScoringExpectation()
    ):
        raise NonReplayableObservationError(
            "An LLM judgment can only replay with the exact expectation used to acquire it."
        )


def _parse_llm_observation(
    *,
    observation: Observation,
    evidence: _ObservationEvidence,
    response_handler: ResponseHandler,
    scorer_identifier: ComponentIdentifier,
    expectation: ScoringExpectation | None,
    category: Sequence[str] | str | None = None,
) -> UnvalidatedScore:
    """
    Parse one retained LLM response without calling a target.

    Returns:
        UnvalidatedScore: The parsed replay result.

    Raises:
        NonReplayableObservationError: If the stored response has no text judgment.
    """
    replay_contract_fingerprint = _replay_contract_fingerprint(
        response_handler=response_handler,
        category=category,
    )
    if observation.payload.replay_contract_fingerprint is None:
        raise NonReplayableObservationError("The response handler does not declare a stable replay contract.")
    if replay_contract_fingerprint != observation.payload.replay_contract_fingerprint:
        raise NonReplayableObservationError("The response handler or category differs from the acquisition contract.")
    if not isinstance(evidence, Message):
        raise NonReplayableObservationError("An LLM judgment requires resolved message evidence.")
    text_piece = next(
        (piece for piece in evidence.message_pieces if piece.converted_value_data_type == "text"),
        None,
    )
    if text_piece is None:
        raise NonReplayableObservationError(f"Observation {observation.id} contains no text judgment.")
    anchor_id = observation.payload.scored_piece_id
    score = response_handler.parse(
        response_text=text_piece.converted_value,
        scorer_identifier=scorer_identifier,
        scored_prompt_id=anchor_id,
        category=category,
        objective=expectation.objective if expectation else None,
    )
    score.message_piece_id = _replay_message_piece_id(observation)
    score.scorable = observation.scorable
    score.scored_expectation = expectation
    score.observation_ids.append(observation.id)
    return score


def _replay_contract_fingerprint(
    *,
    response_handler: ResponseHandler,
    category: Sequence[str] | str | None,
) -> str | None:
    """
    Calculate a stable identity for response parsing and caller-supplied categories.

    Returns:
        str | None: The replay contract digest, or None when the handler is not stable.
    """
    handler_identifier = response_handler._replay_identifier()
    if handler_identifier is None:
        return None
    normalized_category = [category] if isinstance(category, str) else list(category) if category else None
    try:
        serialized = json.dumps(
            {
                "handler": handler_identifier,
                "category": normalized_category,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
