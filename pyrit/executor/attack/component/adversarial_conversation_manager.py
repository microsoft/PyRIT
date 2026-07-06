# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Single-conversation adversarial-chat interaction for multi-turn attacks."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pyrit.exceptions import (
    ComponentRole,
    InvalidJsonException,
    execution_context,
    pyrit_json_retry,
    remove_markdown_json,
)
from pyrit.executor.attack.core.attack_config import (
    resolve_adversarial_json_schema,
)
from pyrit.models import (
    JSON_SCHEMA_METADATA_KEY,
    JsonSchemaDefinition,
    Message,
    Score,
    SeedPrompt,
)
from pyrit.prompt_normalizer import PromptNormalizer

if TYPE_CHECKING:
    from pyrit.executor.attack.component.modality_router import _ModalityFeedbackRouter
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

# The one field of the adversarial-chat schema that the attack loop consumes; the other
# declared fields carry the attacker's own reasoning. The full set of required/permitted keys
# is taken from the resolved schema itself at parse time (see ``_parse_adversarial_reply``).
_NEXT_MESSAGE_KEY = "next_message"


@dataclass
class AdversarialReply:
    """
    Parsed result of one adversarial-chat turn.

    ``next_message`` is always populated: it is the value extracted from the shared
    ``adversarial_chat`` schema when one is declared, otherwise the raw response text.
    ``rationale`` and ``last_response_summary`` are only populated on the schema path.
    """

    next_message: str
    rationale: str | None = None
    last_response_summary: str | None = None
    raw: str = ""


def _camel_to_snake(name: str) -> str:
    """
    Convert a ``camelCase`` or ``PascalCase`` identifier to ``snake_case``.

    Args:
        name: The identifier to convert.

    Returns:
        The snake_case form of the identifier.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# Feedback strings for objective-target responses that carry no usable text.
_BLOCKED_FEEDBACK_TEXT = (
    "Request to target failed: blocked. Please rewrite your prompt to avoid getting blocked next time."
)
_EMPTY_FEEDBACK_TEXT = "The previous response was empty. Please continue."


def _joined_text_value(message: Message) -> str:
    """
    Join the converted values of the message's text pieces with newlines.

    Args:
        message: The message whose text pieces to read.

    Returns:
        The newline-joined text (empty when the message has no text pieces).
    """
    pieces = message.get_pieces_by_type(data_type="text")
    return "\n".join(piece.converted_value for piece in pieces if piece.converted_value)


def _first_response_error(message: Message) -> str:
    """
    Find the response-error code of the first errored piece.

    Args:
        message: The message to scan for an errored piece.

    Returns:
        The first errored piece's response-error code, or ``"none"`` when no piece errored.
    """
    for piece in message.message_pieces:
        if piece.has_error():
            return piece.response_error
    return "none"


def _build_adversarial_feedback_text(
    *,
    last_response: Message,
    score: Score | None,
    use_score_as_feedback: bool,
) -> str:
    """
    Build the per-turn feedback text handed to the adversarial chat from the objective response.

    Blocked and errored responses are detected across *all* message pieces, so a blocked or
    errored piece is never masked by an earlier clean one, and yield a short failure notice.
    Otherwise the objective target's text is used, optionally with the scorer rationale appended
    when ``use_score_as_feedback`` is enabled; a response with neither text nor usable feedback
    nudges the adversarial chat to continue.

    Args:
        last_response: The objective target's latest response.
        score: The score for ``last_response``, or None when the turn was not scored.
        use_score_as_feedback: Whether to append the scorer rationale as feedback.

    Returns:
        The feedback text to render into the adversarial prompt.
    """
    if any(piece.is_blocked() for piece in last_response.message_pieces):
        return _BLOCKED_FEEDBACK_TEXT
    if last_response.is_error():
        return f"Request to target failed: {_first_response_error(last_response)}"

    text = _joined_text_value(last_response)
    rationale = score.score_rationale if use_score_as_feedback and score is not None and score.score_rationale else None
    if text:
        return f"{text}\n\n{rationale}" if rationale else text
    if rationale:
        return rationale
    return _EMPTY_FEEDBACK_TEXT


def _build_adversarial_prompt_metadata(*, response_json_schema: JsonSchemaDefinition | None) -> dict[str, Any]:
    """
    Build the adversarial-chat request metadata for an optional response schema.

    When a schema is declared, returns ``response_format`` plus the shared schema under
    ``JSON_SCHEMA_METADATA_KEY`` so schema-aware targets can natively constrain the reply.
    When no schema is declared, returns an empty dict so the raw-text behavior is unchanged.

    Args:
        response_json_schema: The schema to forward, or None.

    Returns:
        The prompt metadata dict (empty when no schema).
    """
    if response_json_schema is None:
        return {}
    return {"response_format": "json", JSON_SCHEMA_METADATA_KEY: response_json_schema}


def _parse_adversarial_reply(response_text: str, *, schema: JsonSchemaDefinition) -> AdversarialReply:
    """
    Parse and validate a JSON reply against the shared ``adversarial_chat`` ``schema``.

    Required and permitted keys are read from ``schema`` itself — its ``required`` list and
    ``properties`` map, honoring ``additionalProperties`` — rather than a hard-coded copy, so the
    schema stays the single source of truth and the parser cannot drift from ``adversarial_chat.yaml``.
    It is the same schema the manager forwards to constrain the target, so the reply is validated
    against exactly what was requested. Markdown code fences are stripped and keys are normalized from
    camelCase to snake_case before validation, so a backend that drifts to ``nextMessage`` still parses
    without burning a retry. ``next_message`` is the one field the attack loop consumes and is always
    required; ``rationale`` / ``last_response_summary`` carry the attacker's own reasoning.

    Args:
        response_text: The raw adversarial-chat reply.
        schema: The resolved response JSON schema to validate against.

    Returns:
        AdversarialReply: The parsed message and reasoning fields.

    Raises:
        InvalidJsonException: If the reply is not valid JSON, is missing a required key, carries a
            key the schema forbids, or omits ``next_message``.
    """
    cleaned = remove_markdown_json(response_text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise InvalidJsonException(message=f"Invalid JSON encountered: {cleaned}") from e

    normalized = {_camel_to_snake(key): value for key, value in parsed.items()}

    required_keys = {_camel_to_snake(key) for key in schema.get("required", [])}
    missing_keys = required_keys - normalized.keys()
    if missing_keys:
        raise InvalidJsonException(message=f"Missing required keys {missing_keys} in JSON response: {cleaned}")

    if schema.get("additionalProperties", True) is False:
        allowed_keys = {_camel_to_snake(key) for key in schema.get("properties", {})}
        extra_keys = normalized.keys() - allowed_keys
        if extra_keys:
            raise InvalidJsonException(message=f"Unexpected keys {extra_keys} found in JSON response: {cleaned}")

    if _NEXT_MESSAGE_KEY not in normalized:
        raise InvalidJsonException(
            message=f"Response is missing the '{_NEXT_MESSAGE_KEY}' field the attack loop sends: {cleaned}"
        )

    return AdversarialReply(
        next_message=str(normalized[_NEXT_MESSAGE_KEY]),
        rationale=normalized.get("rationale"),
        last_response_summary=normalized.get("last_response_summary"),
        raw=response_text,
    )


class AdversarialConversationManager:
    """
    Drives a single adversarial-chat conversation for a multi-turn attack.

    One manager owns one adversarial conversation (identified by ``conversation_id``): the
    conversation id is what preserves the adversarial chat's own running history across turns.
    Crescendo, TAP, PAIR, and Red Teaming would otherwise each hand-roll the recurring
    mechanics this component centralizes:

    1. Holding the resolved adversarial system prompt, the (optional) first message, the
       per-turn prompt template, and the single response JSON schema declared on either prompt.
    2. Building per-turn prompt metadata — ``response_format`` plus the shared schema —
       only when a schema is declared, so schema-aware targets natively constrain the
       response shape.
    3. Sending the turn to the adversarial target on this manager's ``conversation_id``.
    4. Parsing the shared ``adversarial_chat`` schema (``next_message`` / ``rationale`` /
       ``last_response_summary``) out of the reply when a schema is declared.

    Conversation context (``conversation_id``, ``objective``, the objective target's
    conversation id, the attack strategy name, and memory labels) is supplied once at
    construction time and reused for every turn, so ``get_next_message_async`` only needs
    the objective target's latest response and its score. The manager folds these into the
    adversarial prompt itself: it computes the per-turn feedback text in Python (handling
    blocked/error/empty responses and optional score feedback) and renders it into
    ``adversarial_prompt_template`` as ``feedback_text``, so callers no longer hand-roll that text.

    First message: ``first_message`` is the *first* user turn sent to the
    adversarial chat (rendered with ``{{ objective }}``) when there is no objective-target
    response yet; it is not re-sent on later turns.

    When no schema is declared, ``get_next_message_async`` attaches no prompt metadata and
    returns the raw response text as ``next_message``.
    """

    def __init__(
        self,
        *,
        adversarial_target: PromptTarget,
        system_prompt: SeedPrompt,
        first_message: SeedPrompt | None = None,
        adversarial_prompt_template: SeedPrompt,
        raise_on_invalid_json: bool = True,
        prompt_normalizer: PromptNormalizer | None = None,
        conversation_id: str | None = None,
        objective: str | None = None,
        objective_target_conversation_id: str | None = None,
        attack_strategy_name: str | None = None,
        memory_labels: dict[str, str] | None = None,
        modality_router: _ModalityFeedbackRouter | None = None,
        use_score_as_feedback: bool = False,
    ) -> None:
        """
        Initialize the adversarial conversation manager.

        Args:
            adversarial_target: The adversarial chat target to send turns to.
            system_prompt: The resolved adversarial system-prompt SeedPrompt.
            first_message: The first message sent to the adversarial chat when there is no
                objective-target response yet (rendered with ``{{ objective }}``), or None for
                strategies that have no first-message seed.
            adversarial_prompt_template: Template rendered each turn to wrap the computed
                per-turn feedback text. Receives ``feedback_text`` and ``objective`` and is
                rendered strictly. Defaults are applied by ``AttackAdversarialConfig``; the
                manager expects a resolved template.
            raise_on_invalid_json: When True (default) and a response schema is declared, a reply
                that fails to match the declared schema raises ``InvalidJsonException`` (retried via
                ``pyrit_json_retry``). When False, the raw reply text is returned as ``next_message``
                instead of raising.
            prompt_normalizer: The prompt normalizer to send through. Defaults to a new one.
            conversation_id: The adversarial-chat conversation id this manager drives. A fresh
                id is generated when None.
            objective: The attack objective (for first-message rendering and execution context).
            objective_target_conversation_id: The objective target's conversation id (for
                execution-context correlation).
            attack_strategy_name: Name of the calling attack strategy (for execution context).
            memory_labels: Optional memory labels to attach to each request.
            modality_router: Optional capability-aware router. When provided, the outgoing
                adversarial message is built via ``build_adversarial_input_message`` so first-turn
                seed media and prior objective-response media are forwarded to the adversarial chat
                when its declared capabilities allow it. When None, a text-only message is sent.
            use_score_as_feedback: When True, the computed per-turn ``feedback_text`` appends the
                scorer rationale to the objective target's response. Defaults to False.

        Raises:
            ValueError: If a response JSON schema is declared on both the system prompt and the
                first message, or if a declared schema omits the ``next_message`` property that the
                attack loop consumes.
        """
        self._adversarial_target = adversarial_target
        self._system_prompt = system_prompt
        self._first_message = first_message
        self._adversarial_prompt_template = adversarial_prompt_template
        self._raise_on_invalid_json = raise_on_invalid_json
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()
        self._conversation_id = conversation_id or str(uuid4())
        self._objective = objective
        self._objective_target_conversation_id = objective_target_conversation_id
        self._attack_strategy_name = attack_strategy_name
        self._memory_labels = memory_labels
        self._modality_router = modality_router
        self._use_score_as_feedback = use_score_as_feedback

        # The single response schema is resolved from the system prompt / first-message
        # template (raising if both declare one), so callers never pass it in.
        self._response_json_schema = resolve_adversarial_json_schema(
            system_prompt=system_prompt,
            first_message=first_message,
        )
        # The attack loop consumes ``next_message``, so a declared schema that omits that
        # property cannot drive this manager — fail fast at construction rather than mid-run.
        declared_schema = self._response_json_schema
        if declared_schema is not None and _NEXT_MESSAGE_KEY not in declared_schema.get("properties", {}):
            raise ValueError(
                f"The adversarial response schema must declare a '{_NEXT_MESSAGE_KEY}' property; "
                "it is the field the attack loop sends to the objective target."
            )

    @property
    def adversarial_target(self) -> PromptTarget:
        """The adversarial chat target."""
        return self._adversarial_target

    @property
    def system_prompt(self) -> SeedPrompt:
        """The resolved adversarial system-prompt SeedPrompt."""
        return self._system_prompt

    @property
    def first_message(self) -> SeedPrompt | None:
        """The resolved adversarial first-message SeedPrompt, if any."""
        return self._first_message

    @property
    def adversarial_prompt_template(self) -> SeedPrompt:
        """The per-turn template that builds the adversarial-chat prompt from a response."""
        return self._adversarial_prompt_template

    @adversarial_prompt_template.setter
    def adversarial_prompt_template(self, value: SeedPrompt) -> None:
        """Allow an attack to swap in a different per-turn adversarial prompt template."""
        self._adversarial_prompt_template = value

    @property
    def conversation_id(self) -> str:
        """The adversarial-chat conversation id this manager drives."""
        return self._conversation_id

    @property
    def response_json_schema(self) -> JsonSchemaDefinition | None:
        """The single response JSON schema, or None when the adversarial chat is raw-text."""
        return self._response_json_schema

    @property
    def has_schema(self) -> bool:
        """Whether a response JSON schema is declared (i.e. the JSON path is active)."""
        return self._response_json_schema is not None

    def _render_first_message(self) -> str:
        """
        Render the first message with this manager's objective.

        Returns:
            The rendered first-turn prompt text.

        Raises:
            ValueError: If no first message is configured, or the first message references
                ``objective`` but none was configured.
        """
        template = self._first_message
        if template is None:
            raise ValueError("No first message configured on AdversarialConversationManager")
        needs_objective = "objective" in (template.parameters or []) or "objective" in template.value
        if self._objective is None and needs_objective:
            raise ValueError("No objective configured to render the first message")
        return template.render_template_value_silent(objective=self._objective)

    def _render_adversarial_prompt(self, *, score: Score | None, last_response: Message) -> str:
        """
        Render the per-turn adversarial prompt from the objective target's response and score.

        The blocked/error/empty/score-feedback branching is computed in Python via
        ``_build_adversarial_feedback_text``; the resulting ``feedback_text`` (plus ``objective``)
        is rendered into ``adversarial_prompt_template``. Rendering is strict, so a template that
        references any other variable raises rather than silently producing empty output.

        Args:
            score: The score for ``last_response``, or None when the turn was not scored.
            last_response: The objective target's latest response.

        Returns:
            The rendered adversarial-chat prompt text.
        """
        feedback_text = _build_adversarial_feedback_text(
            last_response=last_response,
            score=score,
            use_score_as_feedback=self._use_score_as_feedback,
        )
        return self._adversarial_prompt_template.render_template_value(
            feedback_text=feedback_text,
            objective=self._objective,
        )

    async def get_first_message_async(self, *, seed_message: Message | None = None) -> AdversarialReply:
        """
        Get the opening adversarial-chat message for this conversation.

        Renders ``first_message`` with the manager's objective and sends it on this manager's
        conversation id. Used for the first turn, when there is no objective-target response
        to react to yet.

        Returns:
            AdversarialReply: ``next_message`` plus parsed extras (schema path) or the raw
                text (raw path).

        Raises:
            ValueError: If no first message / objective is configured, or no response is
                received from the adversarial chat.
            InvalidJsonException: If a schema is declared but the reply is invalid.
        """
        return await self._send_and_parse_async(
            prompt_text=self._render_first_message(),
            seed_message=seed_message,
        )

    async def get_next_message_async(
        self,
        *,
        score: Score | None,
        last_response: Message,
        seed_message: Message | None = None,
    ) -> AdversarialReply:
        """
        Get the next message from the adversarial chat for this conversation.

        The objective target's latest response and its score are folded into the adversarial
        prompt via ``adversarial_prompt_template`` before being sent on this manager's
        conversation id.

        Args:
            score: The score for ``last_response``, or None when the turn was not scored
                (e.g. intermediate turns with ``score_last_turn_only``).
            last_response: The objective target's latest response — the message the
                adversarial chat reacts to this turn.
            seed_message: Optional seed message whose media pieces should be forwarded to the
                adversarial chat when a ``modality_router`` is configured.

        Returns:
            AdversarialReply: ``next_message`` plus parsed extras (schema path) or the raw
                text (raw path).

        Raises:
            ValueError: If no response is received from the adversarial chat.
            InvalidJsonException: If a schema is declared but the reply is not valid JSON
                or is missing/has unexpected keys.
        """
        prompt_text = self._render_adversarial_prompt(score=score, last_response=last_response)
        return await self._send_and_parse_async(
            prompt_text=prompt_text,
            last_response=last_response,
            seed_message=seed_message,
        )

    @pyrit_json_retry
    async def _send_and_parse_async(
        self,
        *,
        prompt_text: str,
        last_response: Message | None = None,
        seed_message: Message | None = None,
    ) -> AdversarialReply:
        """
        Send one user turn to the adversarial chat and parse its reply.

        This is the single place adversarial-chat JSON retry lives: when a schema is declared
        and the reply fails to match it, ``InvalidJsonException`` propagates and ``pyrit_json_retry``
        re-sends the turn until it parses or the attempt budget is exhausted. When
        ``raise_on_invalid_json`` is False, an unparseable reply is returned as raw text instead.

        When a ``modality_router`` is configured, the outgoing message is built via
        ``build_adversarial_input_message`` so first-turn seed media (``seed_message``) and prior
        objective-response media (``last_response``) are forwarded to the adversarial chat when its
        declared capabilities allow it; otherwise a text-only message is sent.

        Args:
            prompt_text: The text to send to the adversarial chat.
            last_response: The objective target's latest response, whose media may be forwarded.
            seed_message: The seed message whose media may be forwarded on the first turn.

        Returns:
            AdversarialReply: ``next_message`` plus parsed extras (schema path) or the raw
                text (raw path).

        Raises:
            ValueError: If no response is received from the adversarial chat.
            InvalidJsonException: If a schema is declared, ``raise_on_invalid_json`` is True, and
                the reply is invalid.
        """
        prompt_metadata = _build_adversarial_prompt_metadata(response_json_schema=self._response_json_schema)

        if self._modality_router is not None:
            message = self._modality_router.build_adversarial_input_message(
                text=prompt_text,
                last_response=last_response,
                seed_message=seed_message,
                prompt_metadata=prompt_metadata or None,
            )
        else:
            message = Message.from_prompt(
                prompt=prompt_text,
                role="user",
                prompt_metadata=prompt_metadata or None,
            )

        with execution_context(
            component_role=ComponentRole.ADVERSARIAL_CHAT,
            attack_strategy_name=self._attack_strategy_name,
            component_identifier=self._adversarial_target.get_identifier(),
            objective_target_conversation_id=self._objective_target_conversation_id,
            objective=self._objective,
        ):
            response = await self._prompt_normalizer.send_prompt_async(
                message=message,
                conversation_id=self._conversation_id,
                target=self._adversarial_target,
                labels=self._memory_labels,
            )

        if not response:
            raise ValueError("No response received from adversarial chat")

        raw = response.get_value()

        schema = self._response_json_schema
        if schema is None:
            return AdversarialReply(next_message=raw, raw=raw)

        if not self._raise_on_invalid_json:
            try:
                return _parse_adversarial_reply(raw, schema=schema)
            except InvalidJsonException:
                return AdversarialReply(next_message=raw, raw=raw)

        return _parse_adversarial_reply(raw, schema=schema)
