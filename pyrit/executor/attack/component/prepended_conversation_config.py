# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pyrit.message_normalizer import (
    ConversationContextNormalizer,
    HistorySquashNormalizer,
    MessageListNormalizer,
    MessageStringNormalizer,
)
from pyrit.models import ChatMessageRole  # noqa: TC001 - public annotation must resolve at runtime
from pyrit.prompt_target.common.target_capabilities import CapabilityName

if TYPE_CHECKING:
    from pyrit.executor.attack.component.prepended_history_send_context import (
        PrependedHistorySendContext,
    )
    from pyrit.models import Message
    from pyrit.prompt_target.common.prompt_target import PromptTarget


@dataclass
class PrependedConversationConfig:
    """
    Configuration for controlling how prepended conversations are processed before
    being sent to the objective target.

    This class provides control over:
    - Which message roles should have request converters applied
    - How targets without editable history format prepended messages with live requests

    Prepended messages remain role-structured in memory. Request converters are applied to
    configured roles before a target without editable history renders that history and the
    applicable live request together (via ``message_normalizer``; default: ConversationContextNormalizer).
    Those converters must produce text because string normalization cannot preserve converted
    image, audio, or other non-text output.
    """

    # Request converters default to prepended user messages only. Every non-user role,
    # including system and simulated assistant history, requires explicit opt-in.
    apply_converters_to_roles: list[ChatMessageRole] = field(default_factory=lambda: ["user"])

    # Optional normalizer to format prepended history and a live request as one text block.
    # Must implement MessageStringNormalizer (e.g., TokenizerTemplateNormalizer or ConversationContextNormalizer).
    # When None and adaptation is needed, a default ConversationContextNormalizer is used
    # that produces "Turn N: User/Assistant" format.
    message_normalizer: MessageStringNormalizer | None = None

    def __post_init__(self) -> None:
        """Normalize simulated assistant opt-in to its API-compatible role."""
        self.apply_converters_to_roles = [
            "assistant" if role == "simulated_assistant" else role for role in self.apply_converters_to_roles
        ]

    def get_message_normalizer(self) -> MessageStringNormalizer:
        """
        Get the normalizer for objective target context, with a default fallback.

        Returns:
            The configured objective_target_context_normalizer, or a default
            ConversationContextNormalizer if none was configured.
        """
        return self.message_normalizer or ConversationContextNormalizer()

    def get_normalizer_overrides(
        self,
        *,
        target: PromptTarget,
        prepended_history_send_context: PrependedHistorySendContext | None,
    ) -> dict[CapabilityName, MessageListNormalizer[Message]]:
        """
        Build per-send target normalizer overrides for prepended history.

        Args:
            target: Target that receives the live request.
            prepended_history_send_context: Explicit persisted seed boundary for
                this attack execution.

        Returns:
            Overrides keyed by the capability they adapt.
        """
        if (
            target.configuration.includes(capability=CapabilityName.EDITABLE_HISTORY)
            or prepended_history_send_context is None
            or not prepended_history_send_context.should_include_seed
        ):
            return {}

        return {
            CapabilityName.EDITABLE_HISTORY: HistorySquashNormalizer(
                expected_history_message_count=prepended_history_send_context.bootstrap_message_count,
                message_normalizer=self.get_message_normalizer(),
            )
        }
