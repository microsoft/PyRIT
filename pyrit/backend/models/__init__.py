# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Backend models package.

Pydantic models for API requests and responses.
"""

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
    Message,
    MessagePiece,
    MessagePieceRequest,
    PrependedMessageRequest,
    Score,
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
from pyrit.backend.models.scenarios import (
    ScenarioListResponse,
    ScenarioSummary,
)
from pyrit.backend.models.targets import (
    CreateTargetRequest,
    TargetInstance,
    TargetListResponse,
)

__all__ = [
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
    "Message",
    "MessagePiece",
    "MessagePieceRequest",
    "PrependedMessageRequest",
    "Score",
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
    "ScenarioListResponse",
    "ScenarioSummary",
    # Targets
    "CreateTargetRequest",
    "TargetInstance",
    "TargetListResponse",
]
