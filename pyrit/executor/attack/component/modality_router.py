# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Capability-aware router for multimodal payloads in multi-turn attacks.

The router decides — based on each target's declared ``TargetCapabilities`` —
whether prior media should travel back to the adversarial chat (as feedback) or
forward to the objective target (as a follow-up edit input). Capabilities are
the single source of truth; consumers do not pass a separate filter.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from pyrit.models import Message, MessagePiece
from pyrit.models.literals import MEDIA_PATH_DATA_TYPES, PromptDataType

if TYPE_CHECKING:
    from pyrit.prompt_target.common.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class _ModalityFeedbackRouter:
    """
    Capability-aware utility for building ``Message`` objects between an
    adversarial chat and an objective target in multi-turn attacks.

    Multimodal-aware behavior is driven entirely by the targets' declared
    ``input_modalities``:

    * **Adversarial direction.** The router can attach both first-turn seed
      media (from ``next_message``) and subsequent objective-response media
      (image, audio, video, ...) to the adversarial chat's feedback message,
      but only for data types where the adversarial target advertises a
      ``{text, <data_type>}`` input combo. Otherwise it falls back to a
      text-only message.
    * **Objective direction.** On turn 0 the router either uses the
      adversarial-generated text alone (when the objective accepts ``{text}``)
      or combines it with the seed media supplied via
      ``AttackParameters.next_message`` (when the objective requires media on
      every request). On subsequent turns it appends the previous response's
      media piece(s) iff the objective advertises a ``{text, <data_type>}``
      input combo.

    The router is a pure utility (no I/O, no memory access). Each attack
    constructs one instance in ``__init__`` and consults it at message-build
    time. Target capabilities are snapshotted at construction; if a caller
    mutates target capabilities later, it must create a new router instance.
    """

    def __init__(
        self,
        *,
        adversarial_chat: PromptTarget,
        objective_target: PromptTarget,
    ) -> None:
        """
        Build a router for a specific adversarial/objective target pair.

        Args:
            adversarial_chat: The chat target that generates adversarial prompts.
            objective_target: The target being attacked.

        Notes:
            Capability sets are read once here and cached for deterministic
            routing decisions throughout the attack.
        """
        self._adversarial_chat = adversarial_chat
        self._objective_target = objective_target

        adv_input = adversarial_chat.configuration.capabilities.input_modalities
        # Data types the adversarial target accepts alongside text in a single
        # input combo. Used to gate whether to attach prior objective media to
        # the adversarial feedback message.
        self._adversarial_media_types_with_text: frozenset[PromptDataType] = frozenset(
            data_type for combo in adv_input if "text" in combo for data_type in combo if data_type != "text"
        )

        # Convenience caches keyed off the objective target's input modalities.
        self._objective_input_modalities: frozenset[frozenset[PromptDataType]] = (
            objective_target.configuration.capabilities.input_modalities
        )
        self._objective_text_only_allowed: bool = frozenset({"text"}) in self._objective_input_modalities
        self._objective_media_types_with_text: frozenset[PromptDataType] = frozenset(
            data_type
            for combo in self._objective_input_modalities
            if "text" in combo
            for data_type in combo
            if data_type != "text"
        )

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #
    @property
    def objective_target_requires_media_on_first_turn(self) -> bool:
        """``True`` iff no advertised objective input combo is exactly ``{text}``."""
        return not self._objective_text_only_allowed

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def validate_first_turn_seed(self, *, next_message: Message | None) -> None:
        """
        Fail-fast check that the first-turn objective request can be constructed.

        Intended to be called from each attack's ``_setup_async`` so we surface
        any seed-media gap before opening conversations or setting system
        prompts.

        Args:
            next_message: The ``AttackParameters.next_message`` provided for
                this attack execution. May be ``None``.

        Raises:
            ValueError: If the objective target does not advertise a text-only
                input combo and ``next_message`` does not supply at least one
                non-text piece to serve as the seed.
        """
        if not self.objective_target_requires_media_on_first_turn:
            return

        seed_pieces = self._extract_seed_media_pieces(next_message)
        if not seed_pieces:
            raise ValueError(
                "Objective target does not accept text-only input. Provide a seed "
                "via `next_message=Message(message_pieces=[MessagePiece.adversarial_placeholder(), "
                "<media_piece>])` so the first turn can be constructed."
            )

    # ------------------------------------------------------------------ #
    # Adversarial-direction Message construction
    # ------------------------------------------------------------------ #
    def build_adversarial_input_message(
        self,
        *,
        text: str,
        last_response: Message | None,
        seed_message: Message | None = None,
        prompt_metadata: dict[str, Any] | None = None,
    ) -> Message:
        """
        Build the ``Message`` to send to the adversarial chat for this turn.

        Includes first-turn seed media from ``seed_message`` and previous-response
        media from ``last_response`` iff the adversarial target advertises a
        ``{text, <data_type>}`` input combo for those data types. Otherwise
        returns a text-only message.

        Args:
            text: The textual feedback (scorer rationale or similar) to send.
            last_response: The most recent objective-target response, or
                ``None`` when no response is available yet.
            seed_message: Optional seed message (typically
                ``AttackParameters.next_message``) whose media pieces should be
                forwarded to the adversarial chat on first turn.
            prompt_metadata: Optional metadata to attach to the text piece
                (e.g., Crescendo's ``{"response_format": "json"}`` hint).

        Returns:
            A user-role ``Message`` ready to send to the adversarial chat.
        """
        seed_media_pieces = self._select_forwardable_media_pieces(
            message=seed_message,
            allowed_media_types=self._adversarial_media_types_with_text,
        )
        response_media_pieces = self._select_forwardable_media_pieces(
            message=last_response,
            allowed_media_types=self._adversarial_media_types_with_text,
        )
        media_pieces = [*seed_media_pieces, *response_media_pieces]
        if not media_pieces:
            return Message.from_prompt(prompt=text, role="user", prompt_metadata=prompt_metadata)
        return self._build_multimodal_message(
            text=text,
            media_pieces=media_pieces,
            prompt_metadata=prompt_metadata,
        )

    def response_media_is_forwardable_to_adversarial(self, *, last_response: Message | None) -> bool:
        """
        Whether any media piece of ``last_response`` would be forwarded to the adversarial chat.

        Used to distinguish a genuine media drop (media the adversarial cannot consume) from the
        legitimate multimodal case where the router does forward the media to a capable adversarial
        chat.

        Args:
            last_response: The most recent objective-target response, or None.

        Returns:
            True iff at least one media piece of ``last_response`` matches a ``{text, <data_type>}``
            input combo advertised by the adversarial target.
        """
        return bool(
            self._select_forwardable_media_pieces(
                message=last_response,
                allowed_media_types=self._adversarial_media_types_with_text,
            )
        )

    # ------------------------------------------------------------------ #
    # Objective-direction Message construction
    # ------------------------------------------------------------------ #
    def fill_adversarial_placeholders(
        self,
        *,
        message: Message,
        adversarial_text: str,
    ) -> Message:
        """
        Replace every adversarial-placeholder piece in ``message`` with a real
        text piece containing ``adversarial_text``.

        Non-placeholder pieces are passed through unchanged. The returned
        ``Message`` is a new instance — ``message`` is not mutated. All
        pieces in the result share a fresh ``conversation_id`` so the result
        is valid for sending.

        Args:
            message: A message whose placeholder pieces should be filled in.
            adversarial_text: The text generated by the adversarial chat.

        Returns:
            A new ``Message`` with placeholders replaced.
        """
        shared_conversation_id = str(uuid.uuid4())
        role = message.message_pieces[0].role
        new_pieces: list[MessagePiece] = []
        for piece in message.message_pieces:
            if piece.is_adversarial_placeholder():
                new_pieces.append(
                    MessagePiece(
                        role=role,
                        original_value=adversarial_text,
                        original_value_data_type="text",
                        conversation_id=shared_conversation_id,
                    )
                )
            else:
                new_pieces.append(
                    MessagePiece(
                        role=role,
                        original_value=piece.original_value,
                        original_value_data_type=piece.original_value_data_type,
                        conversation_id=shared_conversation_id,
                    )
                )
        return Message(message_pieces=new_pieces)

    def build_objective_input_message(
        self,
        *,
        text: str,
        last_response: Message | None,
        turn_index: int,
    ) -> Message:
        """
        Build the ``Message`` to send to the objective target for this turn.

        * On ``turn_index == 0`` the message is text-only if the objective
          target advertises ``{text}`` in its input modalities. Otherwise the
          router raises (this is a defensive backstop —
          ``validate_first_turn_seed`` should have been called from the
          attack's ``_setup_async``; the canonical edit-only path goes through
          ``fill_adversarial_placeholders`` instead).
        * On ``turn_index >= 1`` the previous response's media piece(s) are
          appended iff the objective target advertises a
          ``{text, <data_type>}`` input combo for that data type. Otherwise
          the message is text-only.

        Args:
            text: The adversarial-generated text to send.
            last_response: The most recent objective-target response, or
                ``None``.
            turn_index: Zero-based index of the current turn.

        Returns:
            A user-role ``Message`` ready to send to the objective target.

        Raises:
            ValueError: If ``turn_index == 0`` and the objective target does
                not advertise a text-only input combo.
        """
        if turn_index == 0:
            if not self._objective_text_only_allowed:
                raise ValueError(
                    "Objective target does not accept text-only input on turn 0. "
                    "The attack must combine adversarial text with a seed media "
                    "piece via `fill_adversarial_placeholders` instead."
                )
            return Message.from_prompt(prompt=text, role="user")

        media_pieces = self._select_forwardable_media_pieces(
            message=last_response,
            allowed_media_types=self._objective_media_types_with_text,
        )
        if not media_pieces:
            return Message.from_prompt(prompt=text, role="user")
        return self._build_multimodal_message(text=text, media_pieces=media_pieces)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_seed_media_pieces(next_message: Message | None) -> list[MessagePiece]:
        """Return the non-text pieces of ``next_message`` that count as seed media."""
        if next_message is None:
            return []
        return [
            piece for piece in next_message.message_pieces if piece.converted_value_data_type in MEDIA_PATH_DATA_TYPES
        ]

    @staticmethod
    def _select_forwardable_media_pieces(
        *,
        message: Message | None,
        allowed_media_types: frozenset[PromptDataType],
    ) -> list[MessagePiece]:
        """
        Return the subset of ``message`` media pieces eligible for forwarding.

        Args:
            message: The source message (seed or response), or ``None``.
            allowed_media_types: Data types the consumer accepts together with text.

        Returns:
            The subset of ``message`` media pieces whose data type is in
            ``allowed_media_types``. Empty when nothing matches or
            ``message`` is ``None``.
        """
        if message is None or not allowed_media_types:
            return []
        return [piece for piece in message.message_pieces if piece.converted_value_data_type in allowed_media_types]

    @staticmethod
    def _build_multimodal_message(
        *,
        text: str,
        media_pieces: list[MessagePiece],
        prompt_metadata: dict[str, str | int] | None = None,
    ) -> Message:
        """
        Build a user-role ``Message`` containing ``text`` plus media copies.

        Args:
            text: The text piece content.
            media_pieces: Media pieces to copy into the new message.
            prompt_metadata: Optional metadata to attach to the text piece.

        Returns:
            A new ``Message`` whose pieces share a single ``conversation_id``
            and pass message validation.
        """
        shared_conversation_id = str(uuid.uuid4())
        text_piece = MessagePiece(
            role="user",
            original_value=text,
            original_value_data_type="text",
            conversation_id=shared_conversation_id,
            prompt_metadata=prompt_metadata or {},
        )
        media_copies = [
            MessagePiece(
                role="user",
                original_value=media_piece.converted_value,
                original_value_data_type=media_piece.converted_value_data_type,
                conversation_id=shared_conversation_id,
            )
            for media_piece in media_pieces
        ]
        return Message(message_pieces=[text_piece, *media_copies])
