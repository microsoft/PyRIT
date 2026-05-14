# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Literal, get_args

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
    message_normalizer: MessageStringNormalizer | None = None

    # Deprecated: this option will be removed in v0.16.0. Setting this field to any
    # non-None value emits a DeprecationWarning. In this release, ``"raise"`` still
    # raises ValueError on non-chat targets; ``"normalize_first_turn"`` and ``None``
    # both normalize the prepended conversation into the first turn (via
    # ``message_normalizer``; default: ConversationContextNormalizer). In v0.16.0
    # non-chat targets will always normalize; there is no replacement for the
    # ``"raise"`` behavior.
    non_chat_target_behavior: Literal["normalize_first_turn", "raise"] | None = None

    def __post_init__(self) -> None:
        """Emit a DeprecationWarning when the deprecated ``non_chat_target_behavior`` field is set."""
        if self.non_chat_target_behavior is not None:
            print_deprecation_message(
                old_item="PrependedConversationConfig(non_chat_target_behavior=...)",
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
        Return a deprecated configuration with ``non_chat_target_behavior="raise"``.

        .. deprecated::
            ``default()`` is deprecated and will be removed in v0.16.0. Use
            ``PrependedConversationConfig()`` instead. In this release the returned
            configuration still raises on non-chat targets; in v0.16.0 the ``"raise"``
            branch is removed and non-chat targets will always normalize the prepended
            conversation into the first turn.

        Returns:
            A configuration equivalent to ``PrependedConversationConfig(non_chat_target_behavior="raise")``.
        """
        print_deprecation_message(
            old_item="PrependedConversationConfig.default()",
            new_item="PrependedConversationConfig() (non-chat targets always normalize the prepended conversation)",
            removed_in="0.16.0",
        )
        # Suppress the __post_init__ deprecation warning so callers see exactly
        # one warning (the one for default()) rather than two for a single deprecated call.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return cls(non_chat_target_behavior="raise")

    @classmethod
    def for_non_chat_target(
        cls,
        *,
        message_normalizer: MessageStringNormalizer | None = None,
        apply_converters_to_roles: list[ChatMessageRole] | None = None,
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
        # Suppress the __post_init__ deprecation warning so callers see exactly one
        # warning (the one for for_non_chat_target()) rather than two.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return cls(
                apply_converters_to_roles=(
                    apply_converters_to_roles
                    if apply_converters_to_roles is not None
                    else list(get_args(ChatMessageRole))
                ),
                message_normalizer=message_normalizer,
                non_chat_target_behavior="normalize_first_turn",
            )
