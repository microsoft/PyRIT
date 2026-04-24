# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, get_args

from pyrit.message_normalizer import (
    ConversationContextNormalizer,
    MessageStringNormalizer,
)
from pyrit.models import ChatMessageRole


@dataclass
class PrependedConversationConfig:
    """
    Configuration for controlling how prepended conversations are processed before
    being sent to the objective target.

    This class provides control over:
    - Which message roles should have request converters applied
    - How to normalize conversation history for non-chat objective targets
    - What to do when the objective target is not a PromptChatTarget
    """

    # Roles for which request converters should be applied to prepended messages.
    # By default, converters are applied to all roles.
    # Example: ["user"] to apply converters only to user messages.
    apply_converters_to_roles: list[ChatMessageRole] = field(default_factory=lambda: list(get_args(ChatMessageRole)))

    # Optional normalizer to format conversation history into a single text block.
    # Must implement MessageStringNormalizer (e.g., TokenizerTemplateNormalizer or ConversationContextNormalizer).
    # When None and normalization is needed (e.g., for non-chat targets), a default
    # ConversationContextNormalizer is used that produces "Turn N: User/Assistant" format.
    message_normalizer: Optional[MessageStringNormalizer] = None

    # Behavior when the target is a PromptTarget but not a PromptChatTarget:
    # - "normalize_first_turn": Normalize the prepended conversation into a string and
    #   store it in ConversationState.normalized_prepended_context. This context will be
    #   prepended to the first message sent to the target. Uses objective_target_context_normalizer
    #   if provided, otherwise falls back to ConversationContextNormalizer.
    # - "raise": Raise a ValueError. Use this when prepended conversation history must be
    #   maintained by the target (i.e., target must be a PromptChatTarget).
    non_chat_target_behavior: Literal["normalize_first_turn", "raise"] = "normalize_first_turn"

    def get_message_normalizer(self) -> MessageStringNormalizer:
        """
        Get the normalizer for objective target context, with a default fallback.

        Returns:
            The configured objective_target_context_normalizer, or a default
            ConversationContextNormalizer if none was configured.
        """
        return self.message_normalizer or ConversationContextNormalizer()

    @classmethod
    def default(cls) -> PrependedConversationConfig:
        """
        Create a default configuration with converters applied to all roles.

        Returns:
            A configuration that applies converters to all prepended messages,
            raising an error for non-chat targets.
        """
        return cls(
            apply_converters_to_roles=list(get_args(ChatMessageRole)),
            message_normalizer=None,
            non_chat_target_behavior="raise",
        )

    @classmethod
    def for_non_chat_target(
        cls,
        *,
        message_normalizer: Optional[MessageStringNormalizer] = None,
        apply_converters_to_roles: Optional[list[ChatMessageRole]] = None,
    ) -> PrependedConversationConfig:
        """
        Create a configuration for use with non-chat targets.

        This configuration normalizes the prepended conversation into a text block
        that will be prepended to the first message sent to the target.

        Args:
            message_normalizer: Normalizer for formatting the prepended conversation into a string.
                Defaults to ConversationContextNormalizer if not provided.
            apply_converters_to_roles: Roles to apply converters to before normalization.
                Defaults to all roles.

        Returns:
            A configuration that normalizes the prepended conversation for non-chat targets.
        """
        return cls(
            apply_converters_to_roles=(
                apply_converters_to_roles if apply_converters_to_roles is not None else list(get_args(ChatMessageRole))
            ),
            message_normalizer=message_normalizer,
            non_chat_target_behavior="normalize_first_turn",
        )
