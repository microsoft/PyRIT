# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Backend models package.

Pydantic models for API requests and responses.
"""

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
from pyrit.backend.models.initializers import (
    InitializerParameterSummary,
    ListRegisteredInitializersResponse,
    RegisteredInitializer,
    RegisterInitializerRequest,
)
from pyrit.backend.models.scenarios import (
    ListRegisteredScenariosResponse,
    RegisteredScenario,
    ScenarioParameterSummary,
)
from pyrit.backend.models.targets import (
    CreateTargetRequest,
    TargetCapabilitiesInfo,
    TargetInstance,
    TargetListResponse,
)

__all__ = [
    # Media
    "DEFAULT_MEDIA_EXTENSIONS",
    # Attacks
    "AddMessageRequest",
    "AddMessageResponse",
    "AttackConversationsResponse",
    "AttackListResponse",
    "AttackOptionsResponse",
    "AttackSummary",
    "UpdateMainConversationRequest",
    "UpdateMainConversationResponse",
    "ConversationMessagesResponse",
    "ConversationSummary",
    "ConverterOptionsResponse",
    "CreateAttackRequest",
    "CreateAttackResponse",
    "CreateConversationRequest",
    "CreateConversationResponse",
    "MessagePieceRequest",
    "MessagePieceView",
    "MessageView",
    "PrependedMessageRequest",
    "ScoreView",
    "TargetInfo",
    "UpdateAttackRequest",
    # Common
    "SENSITIVE_FIELD_PATTERNS",
    "FieldError",
    "filter_sensitive_fields",
    "PaginationInfo",
    "ProblemDetail",
    # Converters
    "ConverterInstance",
    "ConverterInstanceListResponse",
    "ConverterPreviewRequest",
    "ConverterPreviewResponse",
    "CreateConverterRequest",
    "CreateConverterResponse",
    "PreviewStep",
    # Scenarios
    "ListRegisteredScenariosResponse",
    "RegisteredScenario",
    "ScenarioParameterSummary",
    # Initializers
    "InitializerParameterSummary",
    "ListRegisteredInitializersResponse",
    "RegisteredInitializer",
    "RegisterInitializerRequest",
    # Targets
    "CreateTargetRequest",
    "TargetCapabilitiesInfo",
    "TargetInstance",
    "TargetListResponse",
]
