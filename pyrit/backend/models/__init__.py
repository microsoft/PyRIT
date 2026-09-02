# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Backend models package.

Pydantic models for API requests and responses.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.backend.models._media import DEFAULT_MEDIA_EXTENSIONS
    from pyrit.backend.models.attacks import (
        AddMessageRequest,
        AddMessageResponse,
        AttackConversationsResponse,
        AttackListResponse,
        AttackOptionsResponse,
        AttackSummary,
        ConversationMessagesResponse,
        ConversationSummary,
        ConverterOptionsResponse,
        CreateAttackRequest,
        CreateAttackResponse,
        CreateConversationRequest,
        CreateConversationResponse,
        MessagePieceRequest,
        MessagePieceView,
        MessageView,
        PrependedMessageRequest,
        ScoreView,
        TargetInfo,
        UpdateAttackRequest,
        UpdateMainConversationRequest,
        UpdateMainConversationResponse,
    )
    from pyrit.backend.models.common import (
        SENSITIVE_FIELD_PATTERNS,
        FieldError,
        PaginationInfo,
        ProblemDetail,
        filter_sensitive_fields,
    )
    from pyrit.backend.models.converters import (
        ConverterInstance,
        ConverterInstanceListResponse,
        ConverterPreviewRequest,
        ConverterPreviewResponse,
        CreateConverterRequest,
        CreateConverterResponse,
        PreviewStep,
    )
    from pyrit.backend.models.datasets import DatasetInfo, DatasetListResponse
    from pyrit.backend.models.initializers import (
        ListRegisteredInitializersResponse,
        RegisterInitializerRequest,
    )
    from pyrit.backend.models.scenarios import ListRegisteredScenariosResponse, ScenarioRunListResponse
    from pyrit.backend.models.targets import CreateTargetRequest, TargetListResponse

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "DEFAULT_MEDIA_EXTENSIONS": "pyrit.backend.models._media",
    "AddMessageRequest": "pyrit.backend.models.attacks",
    "AddMessageResponse": "pyrit.backend.models.attacks",
    "AttackConversationsResponse": "pyrit.backend.models.attacks",
    "AttackListResponse": "pyrit.backend.models.attacks",
    "AttackOptionsResponse": "pyrit.backend.models.attacks",
    "AttackSummary": "pyrit.backend.models.attacks",
    "UpdateMainConversationRequest": "pyrit.backend.models.attacks",
    "UpdateMainConversationResponse": "pyrit.backend.models.attacks",
    "ConversationMessagesResponse": "pyrit.backend.models.attacks",
    "ConversationSummary": "pyrit.backend.models.attacks",
    "ConverterOptionsResponse": "pyrit.backend.models.attacks",
    "CreateAttackRequest": "pyrit.backend.models.attacks",
    "CreateAttackResponse": "pyrit.backend.models.attacks",
    "CreateConversationRequest": "pyrit.backend.models.attacks",
    "CreateConversationResponse": "pyrit.backend.models.attacks",
    "MessagePieceRequest": "pyrit.backend.models.attacks",
    "MessagePieceView": "pyrit.backend.models.attacks",
    "MessageView": "pyrit.backend.models.attacks",
    "PrependedMessageRequest": "pyrit.backend.models.attacks",
    "ScoreView": "pyrit.backend.models.attacks",
    "TargetInfo": "pyrit.backend.models.attacks",
    "UpdateAttackRequest": "pyrit.backend.models.attacks",
    "SENSITIVE_FIELD_PATTERNS": "pyrit.backend.models.common",
    "FieldError": "pyrit.backend.models.common",
    "filter_sensitive_fields": "pyrit.backend.models.common",
    "PaginationInfo": "pyrit.backend.models.common",
    "ProblemDetail": "pyrit.backend.models.common",
    "ConverterInstance": "pyrit.backend.models.converters",
    "ConverterInstanceListResponse": "pyrit.backend.models.converters",
    "ConverterPreviewRequest": "pyrit.backend.models.converters",
    "ConverterPreviewResponse": "pyrit.backend.models.converters",
    "CreateConverterRequest": "pyrit.backend.models.converters",
    "CreateConverterResponse": "pyrit.backend.models.converters",
    "PreviewStep": "pyrit.backend.models.converters",
    "DatasetInfo": "pyrit.backend.models.datasets",
    "DatasetListResponse": "pyrit.backend.models.datasets",
    "ListRegisteredScenariosResponse": "pyrit.backend.models.scenarios",
    "ScenarioRunListResponse": "pyrit.backend.models.scenarios",
    "ListRegisteredInitializersResponse": "pyrit.backend.models.initializers",
    "RegisterInitializerRequest": "pyrit.backend.models.initializers",
    "CreateTargetRequest": "pyrit.backend.models.targets",
    "TargetListResponse": "pyrit.backend.models.targets",
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
