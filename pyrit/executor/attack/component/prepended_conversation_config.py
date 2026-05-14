# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Literal, Optional, get_args

from pyrit.common.deprecation import print_deprecation_message
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
    - What to do when the objective target is not a chat-capable PromptTarget
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

    # Behavior when the target is a PromptTarget that does not natively support editable
    # multi-turn history (CapabilityName.EDITABLE_HISTORY):
    # - "normalize_first_turn": Normalize the prepended conversation into a string via
    #   ``message_normalizer`` (default: ConversationContextNormalizer) and prepend the
    #   result to ``context.next_message``.
    # - "raise": Deprecated; this option will be removed in v0.16.0. Non-chat targets
    #   now always normalize the prepended conversation into the first turn; there is
    #   no replacement for the raise behavior.
    non_chat_target_behavior: Literal["normalize_first_turn", "raise"] = "normalize_first_turn"

    def __post_init__(self) -> None:
        if self.non_chat_target_behavior == "raise":
            print_deprecation_message(
                old_item="PrependedConversationConfig(non_chat_target_behavior='raise')",
                new_item="PrependedConversationConfig() (non-chat targets always normalize the prepended conversation)",
                removed_in="0.16.0",
            )

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
        Deprecated factory that returns a configuration with ``non_chat_target_behavior="raise"``.

        .. deprecated::
            ``default()`` is deprecated and will be removed in v0.16.0. Use
            ``PrependedConversationConfig()`` instead. Non-chat targets always
            normalize the prepended conversation into the first turn; the
            ``raise`` behavior is no longer supported.

        Returns:
            A configuration equivalent to ``PrependedConversationConfig(non_chat_target_behavior="raise")``.
        """
        print_deprecation_message(
            old_item="PrependedConversationConfig.default()",
            new_item="PrependedConversationConfig() (non-chat targets always normalize the prepended conversation)",
            removed_in="0.16.0",
        )
        # Suppress the __post_init__ "raise" deprecation warning so callers see exactly
        # one warning (the one for default()) rather than two for a single deprecated call.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return cls(non_chat_target_behavior="raise")

    @classmethod
    def for_non_chat_target(
        cls,
        *,
        message_normalizer: Optional[MessageStringNormalizer] = None,
        apply_converters_to_roles: Optional[list[ChatMessageRole]] = None,
    ) -> PrependedConversationConfig:
        """
        Create a configuration for use with non-chat targets.

        .. deprecated::
            ``for_non_chat_target()`` is deprecated and will be removed in v0.16.0.
            Non-chat targets always normalize the prepended conversation into the
            first turn, so this factory is equivalent to ``PrependedConversationConfig(...)``
            with the same arguments. Use the default constructor instead.

        Args:
            message_normalizer: Normalizer for formatting the prepended conversation into a string.
                Defaults to ConversationContextNormalizer if not provided.
            apply_converters_to_roles: Roles to apply converters to before normalization.
                Defaults to all roles.

        Returns:
            A configuration that normalizes the prepended conversation for non-chat targets.
        """
        print_deprecation_message(
            old_item="PrependedConversationConfig.for_non_chat_target()",
            new_item="PrependedConversationConfig() (non-chat targets always normalize the prepended conversation)",
            removed_in="0.16.0",
        )
        return cls(
            apply_converters_to_roles=(
                apply_converters_to_roles if apply_converters_to_roles is not None else list(get_args(ChatMessageRole))
            ),
            message_normalizer=message_normalizer,
            non_chat_target_behavior="normalize_first_turn",
        )
