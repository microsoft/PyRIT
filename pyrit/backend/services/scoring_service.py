# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scoring service for invoking registered scorers on demand.

This service is the thin glue between the REST surface and ``Scorer.score_async``:

* ``list_scorers_async`` enumerates ``ScorerRegistry`` so the GUI can populate a dropdown.
* ``score_conversation_async`` resolves a scorer by registry name and applies it to either
  the last assistant message in a conversation or the whole concatenated transcript
  (via ``create_conversation_scorer``).
* ``score_message_async`` scores a single message piece in a conversation.

All scoring runs through ``Scorer.score_async`` which persists scores to memory, so a
subsequent ``GET /attacks/{id}/messages`` call will surface the new scores on the
``BackendMessagePiece.scores`` field with no additional work here.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from pyrit.backend.mappers import pyrit_scores_to_dto
from pyrit.backend.models.attacks import Score
from pyrit.backend.models.scoring import (
    CreateCustomScorerRequest,
    CustomScorerConfig,
    CustomScorerResponse,
    GeneralFloatScaleConfig,
    GeneralTrueFalseConfig,
    ScoreConversationMode,
    ScoreConversationRequest,
    ScoreMessageRequest,
    ScoreResponse,
    ScorerListResponse,
    ScorerSummary,
    ThresholdWrapperConfig,
    UpdateCustomScorerRequest,
)
from pyrit.memory import CentralMemory
from pyrit.registry import ScorerRegistry

if TYPE_CHECKING:
    from pyrit.models import Message
    from pyrit.prompt_target import PromptTarget
    from pyrit.score.scorer import Scorer

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Custom (user-created) scorer state
# ----------------------------------------------------------------------
# Holds the original CreateCustomScorerRequest.config for every scorer
# registered through the custom-scorer endpoints. Used (a) to mark scorers
# as editable in ``list_scorers_async``, (b) to return the seed values for
# the edit dialog, and (c) as the gating check that a scorer is allowed to
# be updated or deleted via the custom-scorer API.
#
# Module-level (process-scoped); does NOT survive backend restart — matches
# how converter instances behave today.
_CUSTOM_SCORER_CONFIGS: dict[str, CustomScorerConfig] = {}

# Preferred default chat target names for self-ask custom scorers, in priority
# order. Mirrors what the built-in initializers use (``GPT4O_TEMP9_TARGET``)
# so user-created custom scorers behave the same as the bundled ones.
_DEFAULT_TARGET_PREFERENCES: tuple[str, ...] = (
    "azure_openai_gpt4o_temp9",
    "azure_openai_gpt4o",
)


def _is_chat_capable(target: object) -> bool:
    """
    Return True if ``target`` exposes the chat-completion surface that self-ask scorers need.

    Uses duck typing instead of an ``isinstance(target, PromptChatTarget)`` check because
    the self-ask scorers' own type annotation is ``chat_target: PromptTarget`` and several
    widely-used chat targets (``OpenAIChatTarget``, ``RoundRobinTarget`` wrapping chat
    targets) inherit from ``PromptTarget`` rather than ``PromptChatTarget``.

    Returns:
        bool: True if ``target`` has both ``set_system_prompt`` and ``send_prompt_async``.
    """
    return hasattr(target, "set_system_prompt") and callable(getattr(target, "send_prompt_async", None))


def _prefer_round_robin(target: PromptTarget, target_registry) -> PromptTarget:
    """
    Return the auto-grouped ``RoundRobinTarget`` wrapping ``target`` if one is registered.

    Mirrors ``ScorerInitializer._get_chat_target_prefer_rr`` so user-created custom
    scorers benefit from the same rate-limit distribution that built-in scorers do.
    Falls back to ``target`` unchanged when no round-robin wrapper exists, when the
    initializer helpers cannot be imported, or when the lookup itself fails.

    Returns:
        PromptTarget: The wrapping round-robin target if present, otherwise ``target``.
    """
    try:
        from pyrit.setup.initializers.components.targets import generate_rr_name, get_behavioral_key
    except ImportError:
        return target

    try:
        rr_name = generate_rr_name(get_behavioral_key(target))
    except Exception:  # noqa: BLE001 — defensive fallback; behavioral key is best-effort
        return target

    rr_target = target_registry.get(rr_name)
    if rr_target is not None:
        return rr_target
    return target


def _extract_class_description(cls: type) -> str | None:
    """
    Extract the first paragraph of a class docstring as a short human-readable description.

    Matches the convention used by ``ConverterService.list_converter_catalog_async`` so the
    UI can render scorer and converter info consistently.
    """
    raw_doc = (cls.__doc__ or "").strip()
    if not raw_doc:
        return None
    first_paragraph = raw_doc.split("\n\n")[0]
    cleaned = " ".join(line.strip() for line in first_paragraph.splitlines() if line.strip())
    return cleaned or None


class ScoringService:
    """
    Service that surfaces registered scorers and runs them against stored conversations.

    Scoring writes to memory via ``Scorer.score_async``, so callers do not need to
    persist the returned ``Score`` DTOs themselves.
    """

    def __init__(self) -> None:
        """Initialize the scoring service."""
        self._memory = CentralMemory.get_memory_instance()
        self._registry = ScorerRegistry.get_registry_singleton()

    async def list_scorers_async(self) -> ScorerListResponse:  # pyrit-async-suffix-exempt
        """
        Enumerate every registered scorer (registry name, class, score type, description, tags).

        Returns:
            ScorerListResponse: Registered scorers in registry-name order.
        """
        items = [
            ScorerSummary(
                scorer_registry_name=entry.name,
                scorer_type=entry.instance.__class__.__name__,
                score_type=entry.instance.scorer_type,
                description=_extract_class_description(entry.instance.__class__),
                tags=sorted(entry.tags.keys()) if entry.tags else [],
                uses_objective=bool(entry.instance.uses_objective),
                editable=entry.name in _CUSTOM_SCORER_CONFIGS,
                custom_config=_CUSTOM_SCORER_CONFIGS.get(entry.name),
            )
            for entry in self._registry.get_all_instances()
        ]
        return ScorerListResponse(items=items)

    async def score_conversation_async(
        self,
        *,
        attack_result_id: str,
        conversation_id: str,
        request: ScoreConversationRequest,
    ) -> ScoreResponse:
        """
        Score a conversation belonging to an attack with a registered scorer.

        Args:
            attack_result_id (str): The AttackResult primary key (used to verify existence).
            conversation_id (str): The conversation to score (must belong to the attack).
            request (ScoreConversationRequest): Scorer name, mode, and optional objective.

        Returns:
            ScoreResponse: The scores produced by the scorer (also persisted to memory).

        Raises:
            LookupError: If the attack does not exist.
            ValueError: If the conversation does not belong to the attack, the conversation
                has no scoreable assistant message, or the scorer registry name is unknown.
        """
        self._verify_conversation_belongs_to_attack(attack_result_id=attack_result_id, conversation_id=conversation_id)

        scorer = self._resolve_scorer(request.scorer_registry_name)
        conversation = list(self._memory.get_conversation(conversation_id=conversation_id))

        if not conversation:
            raise ValueError(f"Conversation '{conversation_id}' has no messages to score")

        target_message = self._select_message_for_scoring(conversation=conversation, mode=request.mode)
        effective_scorer = self._maybe_wrap_for_conversation_scoring(scorer=scorer, mode=request.mode)

        scores = await effective_scorer.score_async(message=target_message, objective=request.objective)
        return ScoreResponse(scores=pyrit_scores_to_dto(list(scores)))

    async def score_message_async(
        self,
        *,
        attack_result_id: str,
        conversation_id: str,
        piece_id: str,
        request: ScoreMessageRequest,
    ) -> ScoreResponse:
        """
        Score a single message piece in a conversation with a registered scorer.

        Args:
            attack_result_id (str): The AttackResult primary key (used to verify existence).
            conversation_id (str): The conversation containing the piece.
            piece_id (str): The message-piece id to score.
            request (ScoreMessageRequest): Scorer name and optional objective.

        Returns:
            ScoreResponse: The scores produced by the scorer (also persisted to memory).

        Raises:
            LookupError: If the attack does not exist, or the piece is not in the conversation.
            ValueError: If the conversation does not belong to the attack or the scorer is unknown.
        """
        self._verify_conversation_belongs_to_attack(attack_result_id=attack_result_id, conversation_id=conversation_id)

        scorer = self._resolve_scorer(request.scorer_registry_name)
        conversation = list(self._memory.get_conversation(conversation_id=conversation_id))

        target_message = self._find_message_containing_piece(conversation=conversation, piece_id=piece_id)
        if target_message is None:
            raise LookupError(f"Message piece '{piece_id}' is not part of conversation '{conversation_id}'")

        scores = await scorer.score_async(message=target_message, objective=request.objective)
        return ScoreResponse(scores=pyrit_scores_to_dto(list(scores)))

    # ------------------------------------------------------------------
    # Custom (user-created) scorers
    # ------------------------------------------------------------------

    async def create_custom_scorer_async(self, *, request: CreateCustomScorerRequest) -> CustomScorerResponse:
        """
        Instantiate a user-defined scorer and register it under ``request.name``.

        Args:
            request (CreateCustomScorerRequest): The new scorer's name + form config.

        Returns:
            CustomScorerResponse: Fresh ``ScorerSummary`` for the newly registered scorer.

        Raises:
            ValueError: If a scorer with the same name is already registered, the config
                references an unknown wrapped scorer, or no default chat target is available.
        """
        if request.name in self._registry:
            raise ValueError(f"Scorer '{request.name}' is already registered")

        scorer = self._build_custom_scorer(config=request.config)
        self._registry.register_instance(scorer, name=request.name)
        _CUSTOM_SCORER_CONFIGS[request.name] = request.config
        logger.info("Registered custom scorer '%s' (%s)", request.name, type(scorer).__name__)
        return CustomScorerResponse(summary=self._summarize_one(request.name))

    async def update_custom_scorer_async(
        self, *, scorer_id: str, request: UpdateCustomScorerRequest
    ) -> CustomScorerResponse:
        """
        Replace the underlying instance of an existing user-defined scorer.

        The registry name (``scorer_id``) is preserved so existing references in the GUI
        continue to work. Past ``Score`` rows are left untouched — only future scoring
        calls use the new config.

        Args:
            scorer_id (str): The registry name of the scorer to update.
            request (UpdateCustomScorerRequest): The replacement config.

        Returns:
            CustomScorerResponse: Fresh ``ScorerSummary`` for the re-registered scorer.

        Raises:
            ValueError: If ``scorer_id`` is not a user-created scorer, or the new config
                references an unknown wrapped scorer.
        """
        if scorer_id not in _CUSTOM_SCORER_CONFIGS:
            raise ValueError(f"Scorer '{scorer_id}' is not a user-created scorer and cannot be edited")

        scorer = self._build_custom_scorer(config=request.config)
        # ``register_instance`` overwrites the existing entry under the same key, so the
        # registry name is preserved across the swap.
        self._registry.register_instance(scorer, name=scorer_id)
        _CUSTOM_SCORER_CONFIGS[scorer_id] = request.config
        logger.info("Updated custom scorer '%s' (%s)", scorer_id, type(scorer).__name__)
        return CustomScorerResponse(summary=self._summarize_one(scorer_id))

    async def delete_custom_scorer_async(self, *, scorer_id: str) -> None:
        """
        Remove a user-defined scorer from the registry.

        Args:
            scorer_id (str): The registry name of the scorer to delete.

        Raises:
            ValueError: If ``scorer_id`` is not a user-created scorer.
        """
        if scorer_id not in _CUSTOM_SCORER_CONFIGS:
            raise ValueError(f"Scorer '{scorer_id}' is not a user-created scorer and cannot be deleted")

        # No public unregister method on the base registry — pop the underlying dict
        # entry directly. Keeps parity with how converters delete (none today) and avoids
        # an API surface change just for this feature.
        self._registry._registry_items.pop(scorer_id, None)
        self._registry._metadata_cache = None
        _CUSTOM_SCORER_CONFIGS.pop(scorer_id, None)
        logger.info("Deleted custom scorer '%s'", scorer_id)

    def _summarize_one(self, scorer_registry_name: str) -> ScorerSummary:
        """
        Build a ``ScorerSummary`` for a single registered scorer by name.

        Returns:
            ScorerSummary: Summary populated from the registry entry.

        Raises:
            LookupError: If the scorer is not registered.
        """
        for entry in self._registry.get_all_instances():
            if entry.name != scorer_registry_name:
                continue
            return ScorerSummary(
                scorer_registry_name=entry.name,
                scorer_type=entry.instance.__class__.__name__,
                score_type=entry.instance.scorer_type,
                description=_extract_class_description(entry.instance.__class__),
                tags=sorted(entry.tags.keys()) if entry.tags else [],
                uses_objective=bool(entry.instance.uses_objective),
                editable=entry.name in _CUSTOM_SCORER_CONFIGS,
                custom_config=_CUSTOM_SCORER_CONFIGS.get(entry.name),
            )
        raise LookupError(f"Scorer '{scorer_registry_name}' is not registered")

    def _build_custom_scorer(self, *, config: CustomScorerConfig) -> Scorer:
        """
        Construct a concrete ``Scorer`` instance from a form-driven config.

        Self-ask scorers receive a fixed default chat target resolved via
        ``_get_default_chat_target`` — users cannot pick the judge model from the GUI.

        Returns:
            Scorer: The constructed scorer instance ready to register.

        Raises:
            ValueError: If the config is malformed (e.g. max_value <= min_value), the
                wrapped scorer is missing, or the wrapped scorer is not a FloatScaleScorer.
        """
        # Local imports keep ``pyrit.backend.services.scoring_service`` cheap to import
        # at app startup; the score subpackage is heavy.
        from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
        from pyrit.score.float_scale.self_ask_general_float_scale_scorer import (
            SelfAskGeneralFloatScaleScorer,
        )
        from pyrit.score.true_false.float_scale_threshold_scorer import FloatScaleThresholdScorer
        from pyrit.score.true_false.self_ask_general_true_false_scorer import (
            SelfAskGeneralTrueFalseScorer,
        )
        from pyrit.score.true_false.true_false_score_aggregator import TrueFalseScoreAggregator

        if isinstance(config, GeneralFloatScaleConfig):
            if config.max_value <= config.min_value:
                raise ValueError("max_value must be strictly greater than min_value")
            return SelfAskGeneralFloatScaleScorer(
                chat_target=self._get_default_chat_target(),
                system_prompt_format_string=config.system_prompt_format_string,
                prompt_format_string=config.prompt_format_string,
                category=config.category,
                min_value=config.min_value,
                max_value=config.max_value,
            )

        if isinstance(config, GeneralTrueFalseConfig):
            aggregator = getattr(TrueFalseScoreAggregator, config.score_aggregator)
            return SelfAskGeneralTrueFalseScorer(
                chat_target=self._get_default_chat_target(),
                system_prompt_format_string=config.system_prompt_format_string,
                prompt_format_string=config.prompt_format_string,
                category=config.category,
                score_aggregator=aggregator,
            )

        if isinstance(config, ThresholdWrapperConfig):
            wrapped = self._registry.get(config.wrapped_scorer_registry_name)
            if wrapped is None:
                raise ValueError(f"Wrapped scorer '{config.wrapped_scorer_registry_name}' is not registered")
            if not isinstance(wrapped, FloatScaleScorer):
                raise ValueError(
                    f"Wrapped scorer '{config.wrapped_scorer_registry_name}' is a "
                    f"{type(wrapped).__name__}; FloatScaleThresholdScorer requires a FloatScaleScorer"
                )
            return FloatScaleThresholdScorer(scorer=wrapped, threshold=config.threshold)

        raise ValueError(f"Unsupported custom scorer config: {type(config).__name__}")

    @staticmethod
    def _get_default_chat_target() -> PromptTarget:
        """
        Resolve the chat target used by every self-ask custom scorer.

        Tries the preferred target names from ``_DEFAULT_TARGET_PREFERENCES`` in order
        (matching what the built-in scorer initializers use). When a preferred target is
        found, prefers the auto-grouped ``RoundRobinTarget`` that wraps it, matching the
        behavior of ``ScorerInitializer._get_chat_target_prefer_rr``. Falls back to the
        first registered chat-capable target if none of the preferred names exist.

        Returns:
            PromptTarget: A registered chat-capable ``PromptTarget`` instance.

        Raises:
            ValueError: If no chat-capable target is registered.
        """
        from pyrit.prompt_target import PromptTarget as _PromptTarget
        from pyrit.registry import TargetRegistry

        target_registry = TargetRegistry.get_registry_singleton()

        for preferred_name in _DEFAULT_TARGET_PREFERENCES:
            candidate = target_registry.get(preferred_name)
            if candidate is None or not _is_chat_capable(candidate):
                continue
            return _prefer_round_robin(candidate, target_registry)

        for entry in target_registry.get_all_instances():
            instance = entry.instance
            if isinstance(instance, _PromptTarget) and _is_chat_capable(instance):
                return instance

        raise ValueError(
            "No chat-capable PromptTarget is registered; cannot create a self-ask custom scorer. "
            "Register a chat target via your ~/.pyrit/.pyrit_conf initializer first."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _verify_conversation_belongs_to_attack(self, *, attack_result_id: str, conversation_id: str) -> None:
        """
        Raise ``LookupError`` if the attack does not exist, ``ValueError`` if the
        conversation does not belong to it.
        """
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            raise LookupError(f"Attack '{attack_result_id}' not found")
        if conversation_id not in results[0].get_active_conversation_ids():
            raise ValueError(f"Conversation '{conversation_id}' is not part of attack '{attack_result_id}'")

    def _resolve_scorer(self, scorer_registry_name: str) -> Scorer:
        """Resolve a scorer by registry name; raise ``ValueError`` when missing."""
        scorer = self._registry.get(scorer_registry_name)
        if scorer is None:
            raise ValueError(f"Scorer '{scorer_registry_name}' is not registered")
        return scorer

    @staticmethod
    def _select_message_for_scoring(*, conversation: list[Message], mode: ScoreConversationMode) -> Message:
        """
        Pick the message to hand to ``Scorer.score_async``.

        For ``last_message`` we score only the most recent assistant turn so the result
        is comparable to a per-message score. For ``whole_conversation`` we just pick the
        last message in the conversation — the ``ConversationScorer`` wrapper uses its
        ``conversation_id`` to fetch the full transcript from memory.
        """
        if mode == "whole_conversation":
            return conversation[-1]

        # last_message: find the most recent assistant (or simulated assistant) turn.
        for message in reversed(conversation):
            if message.message_pieces and message.message_pieces[0].role in (
                "assistant",
                "simulated_assistant",
            ):
                return message
        raise ValueError("Conversation has no assistant message to score")

    @staticmethod
    def _maybe_wrap_for_conversation_scoring(*, scorer: Scorer, mode: ScoreConversationMode) -> Scorer:
        """
        Wrap the scorer in a ``ConversationScorer`` when the caller asked for
        whole-conversation scoring. Raises ``ValueError`` if the scorer cannot be wrapped
        (i.e. it isn't a ``FloatScaleScorer`` or ``TrueFalseScorer``).
        """
        if mode != "whole_conversation":
            return scorer

        from pyrit.score.conversation_scorer import create_conversation_scorer
        from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
        from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

        if not isinstance(scorer, (FloatScaleScorer, TrueFalseScorer)):
            raise ValueError(
                "Whole-conversation scoring requires a FloatScaleScorer or TrueFalseScorer; "
                f"got {type(scorer).__name__}"
            )
        return create_conversation_scorer(scorer=scorer)

    @staticmethod
    def _find_message_containing_piece(*, conversation: list[Message], piece_id: str) -> Message | None:
        """Return the message in ``conversation`` whose pieces include ``piece_id``."""
        for message in conversation:
            for piece in message.message_pieces:
                if str(piece.id) == piece_id:
                    return message
        return None


# ============================================================================
# Singleton
# ============================================================================


@lru_cache(maxsize=1)
def get_scoring_service() -> ScoringService:
    """
    Get the global scoring service instance.

    Returns:
        ScoringService: The singleton ``ScoringService`` instance.
    """
    return ScoringService()


__all__ = ["ScoringService", "get_scoring_service", "Score"]
