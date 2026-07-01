# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import get_args

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

    Non-chat objective targets always normalize the prepended conversation into the
    first turn (via ``message_normalizer``; default: ConversationContextNormalizer).
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

    def get_message_normalizer(self) -> MessageStringNormalizer:
        """
        Get the normalizer for objective target context, with a default fallback.

        Returns:
            The configured objective_target_context_normalizer, or a default
            ConversationContextNormalizer if none was configured.
        """
        return self.message_normalizer or ConversationContextNormalizer()
