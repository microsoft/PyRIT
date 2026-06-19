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
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

# Keys of the shared ``adversarial_chat`` JSON schema. The attack loop consumes
# ``next_message``; the other two carry the attacker's own reasoning.
_EXPECTED_KEYS = {"next_message", "rationale", "last_response_summary"}


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


class _MessageBucket:
    """
    The message pieces of a single data type, exposed for template rendering.

    Empty buckets render as blank text so a template like ``{{ message.text.converted_value }}``
    is safe even when the objective target returned no piece of that data type.
    """

    def __init__(self, pieces: list[Any]) -> None:
        self._pieces = pieces

    @property
    def converted_value(self) -> str:
        """The converted values of all pieces in this bucket, newline-joined."""
        return "\n".join(p.converted_value for p in self._pieces if p.converted_value)

    @property
    def original_value(self) -> str:
        """The original values of all pieces in this bucket, newline-joined."""
        return "\n".join(p.original_value for p in self._pieces if p.original_value)


class _MessageView:
    """
    A data-type-bucketed view over a ``Message`` for adversarial-prompt templates.

    ``message.text`` / ``message.image_path`` / ... each yield a ``_MessageBucket`` for that
    converted-value data type (empty when absent). ``message.is_blocked`` / ``message.has_error``
    surface the first piece's status for Jinja conditionals.
    """

    def __init__(self, message: Message) -> None:
        self._message = message

    @property
    def is_blocked(self) -> bool:
        """Whether the first message piece is a blocked response."""
        pieces = self._message.message_pieces
        return bool(pieces) and pieces[0].is_blocked()

    @property
    def has_error(self) -> bool:
        """Whether the first message piece carries an error."""
        pieces = self._message.message_pieces
        return bool(pieces) and pieces[0].has_error()

    def __getattr__(self, data_type: str) -> _MessageBucket:
        return _MessageBucket(self._message.get_pieces_by_type(data_type=data_type))


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


def _parse_adversarial_reply(response_text: str) -> AdversarialReply:
    """
    Parse and validate a JSON reply against the shared ``adversarial_chat`` schema.

    Markdown code fences are stripped and keys are normalized from camelCase to snake_case
    before validation, so a backend that drifts to ``nextMessage`` still parses without
    burning a retry.

    Args:
        response_text: The raw adversarial-chat reply.

    Returns:
        AdversarialReply: The parsed message and reasoning fields.

    Raises:
        InvalidJsonException: If the reply is not valid JSON or has missing/extra keys.
    """
    cleaned = remove_markdown_json(response_text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise InvalidJsonException(message=f"Invalid JSON encountered: {cleaned}") from e

    normalized = {_camel_to_snake(key): value for key, value in parsed.items()}

    missing_keys = _EXPECTED_KEYS - set(normalized.keys())
    if missing_keys:
        raise InvalidJsonException(message=f"Missing required keys {missing_keys} in JSON response: {cleaned}")

    extra_keys = set(normalized.keys()) - _EXPECTED_KEYS
    if extra_keys:
        raise InvalidJsonException(message=f"Unexpected keys {extra_keys} found in JSON response: {cleaned}")

    return AdversarialReply(
        next_message=str(normalized["next_message"]),
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
    adversarial prompt itself via ``adversarial_prompt_template`` (rendering ``objective``,
    ``score``, and a data-type-bucketed ``message`` view), so callers no longer hand-roll
    that text.

    First message: ``adversarial_first_prompt_template`` is the *first* user turn sent to the
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
        adversarial_first_prompt_template: SeedPrompt | None = None,
        adversarial_prompt_template: SeedPrompt,
        raise_on_invalid_json: bool = True,
        prompt_normalizer: PromptNormalizer | None = None,
        conversation_id: str | None = None,
        objective: str | None = None,
        objective_target_conversation_id: str | None = None,
        attack_strategy_name: str | None = None,
        memory_labels: dict[str, str] | None = None,
    ) -> None:
        """
        Initialize the adversarial conversation manager.

        Args:
            adversarial_target: The adversarial chat target to send turns to.
            system_prompt: The resolved adversarial system-prompt SeedPrompt.
            adversarial_first_prompt_template: The first message sent to the adversarial chat
                when there is no objective-target response yet (rendered with ``{{ objective }}``),
                or None for strategies that have no first-message seed.
            adversarial_prompt_template: Template rendered each turn to build the text handed
                to the adversarial chat from the objective target's latest response. Receives
                ``objective``, ``score``, and a data-type-bucketed ``message`` view. Defaults are
                applied by ``AttackAdversarialConfig``; the manager expects a resolved template.
            raise_on_invalid_json: When True (default) and a response schema is declared, a reply
                that fails to match the shared ``adversarial_chat`` schema raises
                ``InvalidJsonException`` (retried via ``pyrit_json_retry``). When False, the raw
                reply text is returned as ``next_message`` instead of raising.
            prompt_normalizer: The prompt normalizer to send through. Defaults to a new one.
            conversation_id: The adversarial-chat conversation id this manager drives. A fresh
                id is generated when None.
            objective: The attack objective (for first-message rendering and execution context).
            objective_target_conversation_id: The objective target's conversation id (for
                execution-context correlation).
            attack_strategy_name: Name of the calling attack strategy (for execution context).
            memory_labels: Optional memory labels to attach to each request.
        """
        self._adversarial_target = adversarial_target
        self._system_prompt = system_prompt
        self._adversarial_first_prompt_template = adversarial_first_prompt_template
        self._adversarial_prompt_template = adversarial_prompt_template
        self._raise_on_invalid_json = raise_on_invalid_json
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()
        self._conversation_id = conversation_id or str(uuid4())
        self._objective = objective
        self._objective_target_conversation_id = objective_target_conversation_id
        self._attack_strategy_name = attack_strategy_name
        self._memory_labels = memory_labels

        # The single response schema is resolved from the system prompt / first-message
        # template (raising if both declare one), so callers never pass it in.
        self._response_json_schema = resolve_adversarial_json_schema(
            system_prompt=system_prompt,
            first_message=adversarial_first_prompt_template,
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
    def adversarial_first_prompt_template(self) -> SeedPrompt | None:
        """The resolved adversarial first-message SeedPrompt, if any."""
        return self._adversarial_first_prompt_template

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
        template = self._adversarial_first_prompt_template
        if template is None:
            raise ValueError("No first message configured on AdversarialConversationManager")
        needs_objective = "objective" in (template.parameters or []) or "objective" in template.value
        if self._objective is None and needs_objective:
            raise ValueError("No objective configured to render the first message")
        return template.render_template_value_silent(objective=self._objective)

    def _render_adversarial_prompt(self, *, score: Score, last_response: Message) -> str:
        """
        Render the per-turn adversarial prompt from the objective target's response and score.

        Args:
            score: The score for ``last_response``.
            last_response: The objective target's latest response.

        Returns:
            The rendered adversarial-chat prompt text.
        """
        return self._adversarial_prompt_template.render_template_value_silent(
            objective=self._objective,
            score=score,
            message=_MessageView(last_response),
        )

    async def get_first_message_async(self) -> AdversarialReply:
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
        return await self._send_and_parse_async(prompt_text=self._render_first_message())

    async def get_next_message_async(
        self,
        *,
        score: Score,
        last_response: Message,
    ) -> AdversarialReply:
        """
        Get the next message from the adversarial chat for this conversation.

        The objective target's latest response and its score are folded into the adversarial
        prompt via ``adversarial_prompt_template`` before being sent on this manager's
        conversation id.

        Args:
            score: The score for ``last_response``.
            last_response: The objective target's latest response — the message the
                adversarial chat reacts to this turn.

        Returns:
            AdversarialReply: ``next_message`` plus parsed extras (schema path) or the raw
                text (raw path).

        Raises:
            ValueError: If no response is received from the adversarial chat.
            InvalidJsonException: If a schema is declared but the reply is not valid JSON
                or is missing/has unexpected keys.
        """
        prompt_text = self._render_adversarial_prompt(score=score, last_response=last_response)
        return await self._send_and_parse_async(prompt_text=prompt_text)

    @pyrit_json_retry
    async def _send_and_parse_async(self, *, prompt_text: str) -> AdversarialReply:
        """
        Send one user turn to the adversarial chat and parse its reply.

        This is the single place adversarial-chat JSON retry lives: when a schema is declared
        and the reply fails to match it, ``InvalidJsonException`` propagates and ``pyrit_json_retry``
        re-sends the turn until it parses or the attempt budget is exhausted. When
        ``raise_on_invalid_json`` is False, an unparseable reply is returned as raw text instead.

        Args:
            prompt_text: The text to send to the adversarial chat.

        Returns:
            AdversarialReply: ``next_message`` plus parsed extras (schema path) or the raw
                text (raw path).

        Raises:
            ValueError: If no response is received from the adversarial chat.
            InvalidJsonException: If a schema is declared, ``raise_on_invalid_json`` is True, and
                the reply is invalid.
        """
        prompt_metadata = _build_adversarial_prompt_metadata(response_json_schema=self._response_json_schema)

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

        if self._response_json_schema is None:
            return AdversarialReply(next_message=raw, raw=raw)

        if not self._raise_on_invalid_json:
            try:
                return _parse_adversarial_reply(raw)
            except InvalidJsonException:
                return AdversarialReply(next_message=raw, raw=raw)

        return _parse_adversarial_reply(raw)
