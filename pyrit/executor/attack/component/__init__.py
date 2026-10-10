# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""Attack components module."""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.executor.attack.component.adversarial_conversation_manager import (
        AdversarialReply,
        AdversarialTurn,
        _AdversarialConversationManager,
    )
    from pyrit.executor.attack.component.conversation_manager import (
        ConversationManager,
        ConversationState,
        build_conversation_context_string_async,
        get_adversarial_chat_messages,
        get_prepended_turn_count,
        mark_messages_as_simulated,
    )
    from pyrit.executor.attack.component.prepended_conversation_config import PrependedConversationConfig

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "_AdversarialConversationManager": "pyrit.executor.attack.component.adversarial_conversation_manager",
    "AdversarialReply": "pyrit.executor.attack.component.adversarial_conversation_manager",
    "AdversarialTurn": "pyrit.executor.attack.component.adversarial_conversation_manager",
    "build_conversation_context_string_async": "pyrit.executor.attack.component.conversation_manager",
    "ConversationManager": "pyrit.executor.attack.component.conversation_manager",
    "ConversationState": "pyrit.executor.attack.component.conversation_manager",
    "get_adversarial_chat_messages": "pyrit.executor.attack.component.conversation_manager",
    "get_prepended_turn_count": "pyrit.executor.attack.component.conversation_manager",
    "mark_messages_as_simulated": "pyrit.executor.attack.component.conversation_manager",
    "PrependedConversationConfig": "pyrit.executor.attack.component.prepended_conversation_config",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
