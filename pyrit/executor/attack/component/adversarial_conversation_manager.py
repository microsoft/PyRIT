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
    DEFAULT_ADVERSARIAL_FIRST_MESSAGE,
    DEFAULT_ADVERSARIAL_PROMPT_TEMPLATE,
    AttackAdversarialConfig,
    resolve_adversarial_json_schema,
    resolve_adversarial_system_prompt,
)
from pyrit.models import (
    JSON_SCHEMA_METADATA_KEY,
    JsonSchemaDefinition,
    Message,
    Score,
    SeedPrompt,
    get_common_json_schema,
)
from pyrit.prompt_normalizer import PromptNormalizer

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.executor.attack.component.modality_router import _ModalityFeedbackRouter
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

# The one field of the adversarial-chat schema that the attack loop consumes; the other
# declared fields carry the attacker's own reasoning. The full set of required/permitted keys
# is taken from the resolved schema itself at parse time (see ``_parse_adversarial_reply``).
_NEXT_MESSAGE_KEY = "next_message"

# Canonical adversarial-chat response schema. Every adversarial-conversation attack (Red Teaming,
# Crescendo, TAP, PAIR, Simulated Conversation) validates against this same schema unless a prompt
# explicitly declares its own, so their adversarial-chat prompts stay interchangeable and a reply is
# always structurally validated instead of silently trusted.
_DEFAULT_ADVERSARIAL_SCHEMA_NAME = "adversarial_chat"


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


@dataclass
class AdversarialTurn:
    """
    Result of one adversarial-conversation turn, ready for the objective target.

    ``objective_message`` is the fully-built ``Message`` the attack sends to the objective target:
    the adversarial chat's next message (with any prior/seed media woven in by the modality router),
    or a caller-supplied seed message sent directly when the adversarial chat is bypassed.
    ``reply`` is the parsed adversarial reply, or None when the adversarial chat was bypassed.
    ``bypassed`` records whether the adversarial chat was skipped this turn.
    """

    objective_message: Message
    reply: AdversarialReply | None = None
    bypassed: bool = False


@dataclass(frozen=True)
class _ResolvedAdversarialConfig:
    """
    The adversarial prompts an attack's ``AttackAdversarialConfig`` resolves to.

    Produced once per attack by ``_AdversarialConversationManager.resolve_config`` — the single owner
    of adversarial-prompt resolution — and reused to build each per-run manager. ``first_message`` and
    ``next_message_template`` are None in override mode (Crescendo, TAP, Simulated Conversation), where
    the attack supplies the adversarial prompt text itself and only the system prompt is resolved.
    """

    system_prompt: SeedPrompt
    first_message: SeedPrompt | None = None
    next_message_template: SeedPrompt | None = None


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


def _parse_adversarial_reply(response_text: str, *, schema: JsonSchemaDefinition | None = None) -> AdversarialReply:
    """
    Parse and validate a JSON reply against the shared ``adversarial_chat`` ``schema``.

    This is the one parser shared by every adversarial-chat executor (Red Teaming, Crescendo, TAP,
    PAIR, Simulated Conversation): they resolve the same ``adversarial_chat`` schema from their prompt
    and hand the reply here, so validation and normalization stay consistent instead of each attack
    hand-rolling its own. Required and permitted keys are read from ``schema`` itself — its ``required``
    list and ``properties`` map, honoring ``additionalProperties`` — rather than a hard-coded copy, so
    the schema stays the single source of truth and cannot drift from ``adversarial_chat.yaml``. When
    ``schema`` is None (no declared schema), only the ``next_message`` invariant is enforced. Markdown
    code fences are stripped and keys are normalized from camelCase to snake_case before validation, so
    a backend that drifts to ``nextMessage`` still parses without burning a retry. ``next_message`` is
    the one field the attack loop consumes and is always required; ``rationale`` /
    ``last_response_summary`` carry the attacker's own reasoning.

    Args:
        response_text: The raw adversarial-chat reply.
        schema: The resolved response JSON schema to validate against, or None to enforce only the
            ``next_message`` invariant.

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

    if schema is not None:
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


class _AdversarialConversationManager:
    """
    Drives a single adversarial-chat conversation for a multi-turn attack.

    One manager owns one adversarial conversation (identified by ``conversation_id``): the
    conversation id is what preserves the adversarial chat's own running history across turns.
    Crescendo, TAP, PAIR, and Red Teaming would otherwise each hand-roll the recurring
    mechanics this component centralizes and owns end to end:

    1. Holding the resolved adversarial system prompt, the (optional) first user message, the
       per-turn next-message template, and the single response JSON schema — defaulting to the
       canonical ``adversarial_chat`` schema when no prompt declares one, so a reply is always
       structurally validated instead of silently trusted.
    2. Setting the adversarial system prompt on the conversation (rendered with ``objective`` and
       ``max_turns``) via ``set_adversarial_system_prompt``.
    3. Building per-turn prompt metadata — ``response_format`` plus the shared schema — so
       schema-aware targets natively constrain the response shape.
    4. Sending the turn to the adversarial target on this manager's ``conversation_id`` and parsing
       the shared ``adversarial_chat`` schema (``next_message`` / ``rationale`` /
       ``last_response_summary``) out of the reply, retrying on invalid JSON.
    5. Building the ready-to-send objective ``Message`` from the reply — weaving in prior-response or
       seed media via the modality router, filling adversarial placeholders, or bypassing the
       adversarial chat entirely when the caller supplies a concrete seed message.

    Conversation context (``conversation_id``, ``objective``, ``max_turns``, the objective target's
    conversation id, the attack strategy name, and memory labels) is supplied once at construction
    time and reused for every turn. Most attacks call the single entry point ``get_next_message_async``
    and receive an ``AdversarialTurn`` whose ``objective_message`` is ready to send — they never
    branch on adversarial placeholders, hand-roll feedback text, or build the objective message
    themselves. Attacks that must inspect the parsed reply *before* deciding what to send (e.g. TAP,
    which scores ``next_message`` for on-topic-ness and may re-prompt with feedback) call
    ``generate_adversarial_reply_async`` for the send/parse half and build the objective message
    themselves.

    Two modes share the same send/parse/schema/modality core:

    * **Template mode** (Red Teaming): the manager computes the per-turn feedback text in Python
      (handling blocked/error/empty responses and optional score feedback) and renders it into the
      first-message / next-message templates.
    * **Override mode** (Crescendo, TAP): the attack supplies the fully-built adversarial prompt text
      via ``adversarial_prompt_text`` (or ``prompt_text``) and the manager does everything else.
    """

    @staticmethod
    def _coerce_seed_prompt(value: str | SeedPrompt | None, *, default: str, error_message: str) -> SeedPrompt:
        """
        Coerce a configured prompt value into a Jinja ``SeedPrompt``.

        Args:
            value: The configured value (inline string, SeedPrompt, or None for the default).
            default: The default template string used when ``value`` is None.
            error_message: The error raised when ``value`` is neither a string nor a SeedPrompt.

        Returns:
            The resolved SeedPrompt.

        Raises:
            ValueError: If ``value`` is not a string, SeedPrompt, or None.
        """
        if value is None:
            value = default
        if isinstance(value, str):
            return SeedPrompt(value=value, data_type="text", is_jinja_template=True)
        if isinstance(value, SeedPrompt):
            return value
        raise ValueError(error_message)

    @classmethod
    def resolve_config(
        cls,
        *,
        config: AttackAdversarialConfig,
        default_system_prompt_path: str | Path,
        system_prompt_required_parameters: list[str],
        system_prompt_error_message: str | None = None,
        resolve_user_messages: bool = False,
    ) -> _ResolvedAdversarialConfig:
        """
        Resolve an ``AttackAdversarialConfig`` into the prompts the manager drives its turns with.

        This is the single owner of adversarial-prompt resolution: it resolves the system prompt
        (inline string / SeedPrompt / default YAML path), coerces the first and next-message templates
        (template mode only), and fails fast when a response schema is declared on both the system
        prompt and the first message. Attacks call this once at construction, store the result, and
        feed it into each per-run manager instead of coercing prompts themselves.

        Args:
            config: The adversarial configuration supplied to the attack.
            default_system_prompt_path: Fallback system-prompt YAML path when the config declares none.
            system_prompt_required_parameters: Parameters the resolved system prompt must support.
            system_prompt_error_message: Optional custom error for system-prompt validation failures.
            resolve_user_messages: When True (template mode, e.g. Red Teaming), coerce
                ``config.first_message`` and ``config.adversarial_prompt_template`` — applying the
                canonical defaults when unset. When False (override mode, e.g. Crescendo / TAP), the
                attack supplies the adversarial prompt text itself, so both are left None.

        Returns:
            _ResolvedAdversarialConfig: The resolved system prompt and (template mode) first / next
            message templates.

        Raises:
            ValueError: If the system prompt is missing required parameters, a response schema is
                declared on both the system prompt and the first message, or a configured prompt value
                is neither a string nor a SeedPrompt.
        """
        system_prompt = resolve_adversarial_system_prompt(
            config=config,
            default_system_prompt_path=default_system_prompt_path,
            required_parameters=system_prompt_required_parameters,
            error_message=system_prompt_error_message,
        )
        first_message: SeedPrompt | None = None
        next_message_template: SeedPrompt | None = None
        if resolve_user_messages:
            first_message = cls._coerce_seed_prompt(
                config.first_message,
                default=DEFAULT_ADVERSARIAL_FIRST_MESSAGE,
                error_message="First message must be a string or SeedPrompt object.",
            )
            next_message_template = cls._coerce_seed_prompt(
                config.adversarial_prompt_template,
                default=DEFAULT_ADVERSARIAL_PROMPT_TEMPLATE,
                error_message="Adversarial prompt template must be a string or SeedPrompt object.",
            )
        # Fail fast when a response schema is declared on both prompts (the per-run manager re-resolves
        # and owns the schema thereafter); the result is discarded here.
        resolve_adversarial_json_schema(system_prompt=system_prompt, first_message=first_message)
        return _ResolvedAdversarialConfig(
            system_prompt=system_prompt,
            first_message=first_message,
            next_message_template=next_message_template,
        )

    def __init__(
        self,
        *,
        adversarial_target: PromptTarget,
        adversarial_system_prompt: SeedPrompt,
        adversarial_first_user_message: SeedPrompt | None = None,
        adversarial_next_user_message: SeedPrompt | None = None,
        max_turns: int = 1,
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
            adversarial_system_prompt: The resolved adversarial system-prompt SeedPrompt. Rendered
                with ``objective`` and ``max_turns`` by ``set_adversarial_system_prompt``.
            adversarial_first_user_message: The first user message sent to the adversarial chat when
                there is no objective-target response yet (rendered with ``{{ objective }}``), or None
                for strategies that build the first turn themselves (override mode).
            adversarial_next_user_message: Template rendered each turn (template mode) to wrap the
                computed per-turn feedback text. Receives ``feedback_text`` and ``objective`` and is
                rendered strictly. May be None in override mode, where the attack supplies the prompt
                text directly.
            max_turns: Maximum number of turns; rendered into the adversarial system prompt as
                ``max_turns``. Defaults to 1.
            raise_on_invalid_json: When True (default), a reply that fails to match the resolved
                schema raises ``InvalidJsonException`` (retried via ``pyrit_json_retry``). When False,
                the raw reply text is returned as ``next_message`` instead of raising.
            prompt_normalizer: The prompt normalizer to send through. Defaults to a new one.
            conversation_id: The adversarial-chat conversation id this manager drives. A fresh
                id is generated when None.
            objective: The attack objective (for first-message / system-prompt rendering and
                execution context).
            objective_target_conversation_id: The objective target's conversation id (for
                execution-context correlation).
            attack_strategy_name: Name of the calling attack strategy (for execution context).
            memory_labels: Optional memory labels to attach to each request.
            modality_router: Optional capability-aware router. When provided, the outgoing
                adversarial message and the objective message forward seed / prior-response media
                when the relevant target's declared capabilities allow it. When None, text-only
                messages are used.
            use_score_as_feedback: When True, the computed per-turn ``feedback_text`` appends the
                scorer rationale to the objective target's response. Defaults to False.

        Raises:
            ValueError: If a response JSON schema is declared on both the system prompt and the
                first message, or if a declared schema omits the ``next_message`` property that the
                attack loop consumes.
        """
        self._adversarial_target = adversarial_target
        self._adversarial_system_prompt = adversarial_system_prompt
        self._adversarial_first_user_message = adversarial_first_user_message
        self._adversarial_next_user_message = adversarial_next_user_message
        self._max_turns = max_turns
        self._raise_on_invalid_json = raise_on_invalid_json
        self._prompt_normalizer = prompt_normalizer or PromptNormalizer()
        self._conversation_id = conversation_id or str(uuid4())
        self._objective = objective
        self._objective_target_conversation_id = objective_target_conversation_id
        self._attack_strategy_name = attack_strategy_name
        self._memory_labels = memory_labels
        self._modality_router = modality_router
        self._use_score_as_feedback = use_score_as_feedback

        # The single response schema is resolved from the system prompt / first-message template
        # (raising if both declare one). When neither declares one, the canonical ``adversarial_chat``
        # schema is used so every reply is validated — there is no unvalidated raw-text path.
        self._response_json_schema: JsonSchemaDefinition = resolve_adversarial_json_schema(
            system_prompt=adversarial_system_prompt,
            first_message=adversarial_first_user_message,
        ) or get_common_json_schema(_DEFAULT_ADVERSARIAL_SCHEMA_NAME)
        # The attack loop consumes ``next_message``, so a declared schema that omits that
        # property cannot drive this manager — fail fast at construction rather than mid-run.
        if _NEXT_MESSAGE_KEY not in self._response_json_schema.get("properties", {}):
            raise ValueError(
                f"The adversarial response schema must declare a '{_NEXT_MESSAGE_KEY}' property; "
                "it is the field the attack loop sends to the objective target."
            )

    @property
    def adversarial_target(self) -> PromptTarget:
        """The adversarial chat target."""
        return self._adversarial_target

    @property
    def adversarial_system_prompt(self) -> SeedPrompt:
        """The resolved adversarial system-prompt SeedPrompt."""
        return self._adversarial_system_prompt

    @property
    def adversarial_first_user_message(self) -> SeedPrompt | None:
        """The resolved adversarial first user-message SeedPrompt, if any."""
        return self._adversarial_first_user_message

    @property
    def adversarial_next_user_message(self) -> SeedPrompt | None:
        """The per-turn template that builds the adversarial-chat prompt from a response."""
        return self._adversarial_next_user_message

    @adversarial_next_user_message.setter
    def adversarial_next_user_message(self, value: SeedPrompt) -> None:
        """Allow an attack to swap in a different per-turn adversarial next-message template."""
        self._adversarial_next_user_message = value

    @property
    def conversation_id(self) -> str:
        """The adversarial-chat conversation id this manager drives."""
        return self._conversation_id

    @property
    def response_json_schema(self) -> JsonSchemaDefinition:
        """The single response JSON schema every reply is validated against."""
        return self._response_json_schema

    def set_adversarial_system_prompt(self, **extra_render_values: object) -> None:
        """
        Render and set the adversarial system prompt on this manager's conversation.

        Renders ``adversarial_system_prompt`` with the manager's ``objective`` and ``max_turns`` and
        sets it on the adversarial target for this manager's ``conversation_id``. Must be called from
        the attack's ``_setup_async`` *before* any prepended adversarial turns are hydrated, because
        ``set_system_prompt`` rejects a conversation that already has messages.

        Args:
            **extra_render_values: Additional attack-specific template variables to render into the
                system prompt (e.g. Crescendo's ``conversation_context``). Attacks that need bespoke
                system-prompt inputs supply them here rather than rendering and setting the prompt
                themselves, keeping the setup mechanics owned by the manager.

        Raises:
            ValueError: If the rendered system prompt is empty.
        """
        rendered = self._adversarial_system_prompt.render_template_value(
            objective=self._objective,
            max_turns=self._max_turns,
            **extra_render_values,
        )
        if not rendered:
            raise ValueError("Adversarial chat system prompt must be defined")
        self._adversarial_target.set_system_prompt(
            system_prompt=rendered,
            conversation_id=self._conversation_id,
        )

    def _render_first_message(self) -> str:
        """
        Render the first message with this manager's objective.

        Returns:
            The rendered first-turn prompt text.

        Raises:
            ValueError: If no first message is configured, or the first message references
                ``objective`` but none was configured.
        """
        template = self._adversarial_first_user_message
        if template is None:
            raise ValueError("No first message configured on the adversarial conversation manager")
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

        Raises:
            ValueError: If no next-message template is configured (override mode should supply the
                prompt text directly instead of calling this).
        """
        feedback_text = _build_adversarial_feedback_text(
            last_response=last_response,
            score=score,
            use_score_as_feedback=self._use_score_as_feedback,
        )
        self._warn_if_response_media_dropped(last_response=last_response, feedback_text=feedback_text)
        if self._adversarial_next_user_message is None:
            raise ValueError("No next-message template configured on the adversarial conversation manager")
        return self._adversarial_next_user_message.render_template_value(
            feedback_text=feedback_text,
            objective=self._objective,
        )

    def _warn_if_response_media_dropped(self, *, last_response: Message, feedback_text: str) -> None:
        """
        Warn when a media-only objective response is silently reduced to a "please continue" nudge.

        When the objective response carries only non-text media that the adversarial chat cannot
        consume (no router, or the router will not forward it), the computed feedback is the empty
        nudge and the media is effectively dropped. That is a likely misconfiguration (e.g. a
        text-only adversarial target paired with an image-generating objective target), so surface a
        warning rather than failing — the legitimate multimodal case, where the router forwards the
        media to a capable adversarial chat, is left silent.

        Args:
            last_response: The objective target's latest response.
            feedback_text: The feedback text computed for this turn.
        """
        if feedback_text != _EMPTY_FEEDBACK_TEXT:
            return
        media_pieces = [piece for piece in last_response.message_pieces if piece.converted_value_data_type != "text"]
        if not media_pieces:
            return
        forwardable = (
            self._modality_router is not None
            and self._modality_router.response_media_is_forwardable_to_adversarial(last_response=last_response)
        )
        if forwardable:
            return
        logger.warning(
            "Objective response carried only non-text media (%d piece(s)) that the adversarial chat "
            "cannot consume; falling back to a 'please continue' nudge. If this is unexpected, ensure "
            "the adversarial target advertises a {text, <media>} input combo so the media is forwarded.",
            len(media_pieces),
        )

    async def get_next_message_async(
        self,
        *,
        turn_index: int,
        seed_message: Message | None = None,
        last_response: Message | None = None,
        score: Score | None = None,
        adversarial_prompt_text: str | None = None,
    ) -> AdversarialTurn:
        """
        Produce the next objective-target message for this adversarial conversation.

        This is the single entry point every adversarial-conversation attack calls each turn. It owns
        the full contract: the bypass path, adversarial prompt selection, the send/parse/schema/retry
        cycle, and building the ready-to-send objective ``Message`` (weaving in prior/seed media or
        filling placeholders). Callers send ``AdversarialTurn.objective_message`` as-is.

        Prompt selection:

        * ``adversarial_prompt_text`` supplied (override mode) — used verbatim as the adversarial turn.
        * else ``last_response is None`` — the first user message is rendered from the objective.
        * else — the next-message template is rendered with the computed per-turn feedback text.

        Objective message:

        * ``seed_message`` with no adversarial placeholder — the adversarial chat is bypassed and a
          duplicate of ``seed_message`` is returned directly (fresh ids, safe to send).
        * ``seed_message`` with adversarial placeholders — the adversarial text fills the placeholder
          slots so caller-supplied seed media travels to the objective target alongside the text.
        * otherwise — the modality router builds the objective request (forwarding prior media when
          the objective target accepts it), or a plain text message when no router is configured.

        Args:
            turn_index: Zero-based index of the current turn (used for objective-message routing).
            seed_message: Optional caller-supplied seed (``AttackParameters.next_message``). Bypasses
                the adversarial chat when it carries no adversarial placeholder.
            last_response: The objective target's latest response, or None on the first turn.
            score: The score for ``last_response``, or None when the turn was not scored.
            adversarial_prompt_text: Optional pre-built adversarial prompt text (override mode). When
                supplied, the manager skips template rendering and sends this text.

        Returns:
            AdversarialTurn: The ready-to-send objective ``Message`` plus the parsed adversarial
                reply (or None when the adversarial chat was bypassed).

        Raises:
            ValueError: If no response is received from the adversarial chat, or a placeholder seed is
                supplied without a modality router to fill it.
            InvalidJsonException: If the reply is not valid JSON or is missing/has unexpected keys.
        """
        has_placeholder = seed_message is not None and any(
            piece.is_adversarial_placeholder() for piece in seed_message.message_pieces
        )

        # Bypass: a concrete caller-supplied seed (no placeholder) is sent as-is. Duplicating gives
        # the objective send fresh ids so the seed message is never mutated or double-persisted.
        if seed_message is not None and not has_placeholder:
            logger.debug("Using custom seed message, bypassing adversarial chat")
            return AdversarialTurn(objective_message=seed_message.duplicate(), reply=None, bypassed=True)

        if adversarial_prompt_text is not None:
            prompt_text = adversarial_prompt_text
        elif last_response is None:
            prompt_text = self._render_first_message()
        else:
            prompt_text = self._render_adversarial_prompt(score=score, last_response=last_response)

        reply = await self._send_and_parse_async(
            prompt_text=prompt_text,
            last_response=last_response,
            seed_message=seed_message,
        )

        objective_message = self._build_objective_message(
            reply=reply,
            seed_message=seed_message if has_placeholder else None,
            last_response=last_response,
            turn_index=turn_index,
        )
        return AdversarialTurn(objective_message=objective_message, reply=reply, bypassed=False)

    async def generate_adversarial_reply_async(
        self,
        *,
        prompt_text: str,
        seed_message: Message | None = None,
        last_response: Message | None = None,
    ) -> AdversarialReply:
        """
        Send a caller-built adversarial prompt and return the parsed reply, without building the
        objective message.

        This is the override-mode send/parse entry point for attacks that need the raw parsed
        ``AdversarialReply`` in hand before building the objective-target message themselves — for
        example TAP, which runs an on-topic scorer on ``next_message`` and may re-prompt the
        adversarial chat with feedback before deciding what to send to the objective target. It owns
        the same metadata/schema, modality-routed message building, execution-context tagging, and
        JSON-retry mechanics as ``get_next_message_async`` (they share ``_send_and_parse_async``); it
        simply stops at the parsed reply instead of weaving the ``next_message`` into an objective
        ``Message``. Attacks whose turn ends with a ready-to-send objective message should call
        ``get_next_message_async`` instead.

        Args:
            prompt_text: The fully-built adversarial prompt text to send this turn.
            seed_message: Optional first-turn seed message whose media the modality router may forward
                to the adversarial chat.
            last_response: The objective target's latest response, whose media the modality router may
                forward to the adversarial chat, or None on the first turn.

        Returns:
            AdversarialReply: ``next_message`` plus the parsed ``rationale`` / ``last_response_summary``.

        Raises:
            ValueError: If no response is received from the adversarial chat.
            InvalidJsonException: If ``raise_on_invalid_json`` is True and the reply is invalid.
        """
        return await self._send_and_parse_async(
            prompt_text=prompt_text,
            last_response=last_response,
            seed_message=seed_message,
        )

    def _build_objective_message(
        self,
        *,
        reply: AdversarialReply,
        seed_message: Message | None,
        last_response: Message | None,
        turn_index: int,
    ) -> Message:
        """
        Build the objective-target message from the adversarial reply.

        Args:
            reply: The parsed adversarial reply whose ``next_message`` drives the objective turn.
            seed_message: The seed message when it carries adversarial placeholders to fill, else None.
            last_response: The objective target's latest response, whose media may be forwarded.
            turn_index: Zero-based index of the current turn.

        Returns:
            Message: The ready-to-send objective-target message.

        Raises:
            ValueError: If ``seed_message`` has placeholders but no modality router is configured.
        """
        if seed_message is not None:
            if self._modality_router is None:
                raise ValueError("An adversarial-placeholder seed requires a modality_router to fill it.")
            return self._modality_router.fill_adversarial_placeholders(
                message=seed_message,
                adversarial_text=reply.next_message,
            )
        if self._modality_router is not None:
            return self._modality_router.build_objective_input_message(
                text=reply.next_message,
                last_response=last_response,
                turn_index=turn_index,
            )
        return Message.from_prompt(prompt=reply.next_message, role="user")

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

        This is the single place adversarial-chat JSON retry lives: when the reply fails to match the
        resolved schema, ``InvalidJsonException`` propagates and ``pyrit_json_retry`` re-sends the turn
        until it parses or the attempt budget is exhausted. When ``raise_on_invalid_json`` is False, an
        unparseable reply is returned as raw text instead.

        When a ``modality_router`` is configured, the outgoing message is built via
        ``build_adversarial_input_message`` so first-turn seed media (``seed_message``) and prior
        objective-response media (``last_response``) are forwarded to the adversarial chat when its
        declared capabilities allow it; otherwise a text-only message is sent.

        Args:
            prompt_text: The text to send to the adversarial chat.
            last_response: The objective target's latest response, whose media may be forwarded.
            seed_message: The seed message whose media may be forwarded on the first turn.

        Returns:
            AdversarialReply: ``next_message`` plus the parsed ``rationale`` / ``last_response_summary``.

        Raises:
            ValueError: If no response is received from the adversarial chat.
            InvalidJsonException: If ``raise_on_invalid_json`` is True and the reply is invalid.
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
            if self._memory_labels:
                for piece in message.message_pieces:
                    piece.labels = self._memory_labels
            response = await self._prompt_normalizer.send_prompt_async(
                message=message,
                conversation_id=self._conversation_id,
                target=self._adversarial_target,
            )

        if not response:
            raise ValueError("No response received from adversarial chat")

        raw = response.get_value()

        schema = self._response_json_schema
        if not self._raise_on_invalid_json:
            try:
                return _parse_adversarial_reply(raw, schema=schema)
            except InvalidJsonException:
                return AdversarialReply(next_message=raw, raw=raw)

        return _parse_adversarial_reply(raw, schema=schema)
