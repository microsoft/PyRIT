# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Functionality to normalize messages into compatible formats for targets.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.message_normalizer.chat_message_normalizer import ChatMessageNormalizer
    from pyrit.message_normalizer.conversation_context_normalizer import ConversationContextNormalizer
    from pyrit.message_normalizer.generic_system_squash import GenericSystemSquashNormalizer
    from pyrit.message_normalizer.history_squash_normalizer import HistorySquashNormalizer
    from pyrit.message_normalizer.json_schema_normalizer import JsonSchemaNormalizer
    from pyrit.message_normalizer.message_normalizer import MessageListNormalizer, MessageStringNormalizer
    from pyrit.message_normalizer.tokenizer_template_normalizer import TokenizerTemplateNormalizer

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "MessageListNormalizer": "pyrit.message_normalizer.message_normalizer",
    "MessageStringNormalizer": "pyrit.message_normalizer.message_normalizer",
    "GenericSystemSquashNormalizer": "pyrit.message_normalizer.generic_system_squash",
    "HistorySquashNormalizer": "pyrit.message_normalizer.history_squash_normalizer",
    "JsonSchemaNormalizer": "pyrit.message_normalizer.json_schema_normalizer",
    "TokenizerTemplateNormalizer": "pyrit.message_normalizer.tokenizer_template_normalizer",
    "ConversationContextNormalizer": "pyrit.message_normalizer.conversation_context_normalizer",
    "ChatMessageNormalizer": "pyrit.message_normalizer.chat_message_normalizer",
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
