# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Public model exports for PyRIT core data structures and helpers.

``pyrit.models`` is the canonical data layer. Files in this package must
import only from the standard library, ``pydantic``,
``pyrit.common.deprecation``, and other ``pyrit.models.*`` submodules. The
CI test ``tests/unit/models/test_import_boundary.py`` enforces this. See
``.github/instructions/models.instructions.md`` for the rule.

Identifier types and helpers live in the ``pyrit.models.identifiers``
sub-package but are re-exported here, so external callers should import them
directly from ``pyrit.models`` (e.g. ``from pyrit.models import
ComponentIdentifier``). The previous ``pyrit.identifiers`` location is kept as
a deprecation shim through ``0.16.0``.
"""

from pyrit.models.attack_result import AttackOutcome, AttackResult, AttackResultT
from pyrit.models.chat_message import (
    ALLOWED_CHAT_MESSAGE_ROLES,
    ChatMessage,
    ChatMessagesDataset,
)
from pyrit.models.conversation_reference import ConversationReference, ConversationType
from pyrit.models.conversation_stats import ConversationStats
from pyrit.models.data_type_serializer import (
    AllowedCategories,
    AudioPathDataTypeSerializer,
    BinaryPathDataTypeSerializer,
    DataTypeSerializer,
    ErrorDataTypeSerializer,
    ImagePathDataTypeSerializer,
    TextDataTypeSerializer,
    VideoPathDataTypeSerializer,
    data_serializer_factory,
)
from pyrit.models.embeddings import EmbeddingData, EmbeddingResponse, EmbeddingSupport, EmbeddingUsageInformation
from pyrit.models.harm_definition import HarmDefinition, ScaleDescription, get_all_harm_definitions
from pyrit.models.identifiers import (
    REGISTRY_NAME_PATTERN,
    TARGET_EVAL_PARAM_FALLBACKS,
    TARGET_EVAL_PARAMS,
    AtomicAttackEvaluationIdentifier,
    ChildEvalRule,
    ComponentIdentifier,
    EvaluationIdentifier,
    Identifiable,
    IdentifierFilter,
    IdentifierType,
    ScorerEvaluationIdentifier,
    build_atomic_attack_identifier,
    build_seed_identifier,
    class_name_to_snake_case,
    compute_eval_hash,
    config_hash,
    snake_case_to_class_name,
    validate_registry_name,
)
from pyrit.models.literals import ChatMessageRole, PromptDataType, PromptResponseError, SeedType
from pyrit.models.message import (
    Message,
    construct_response_from_request,
    group_conversation_message_pieces_by_sequence,
    group_message_pieces_into_conversations,
)
from pyrit.models.message_piece import MessagePiece, sort_message_pieces
from pyrit.models.question_answering import QuestionAnsweringDataset, QuestionAnsweringEntry, QuestionChoice
from pyrit.models.retry_event import RetryEvent
from pyrit.models.scenario_result import ScenarioIdentifier, ScenarioResult
from pyrit.models.score import Score, ScoreType, UnvalidatedScore

# Seeds - import from new seeds submodule for forward compatibility
# Also keep imports from old locations for backward compatibility
from pyrit.models.seeds import (
    NextMessageSystemPromptPaths,
    Seed,
    SeedAttackGroup,
    SeedAttackTechniqueGroup,
    SeedDataset,
    SeedGroup,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
    SimulatedTargetSystemPromptPaths,
)

# Keep old module-level imports working (deprecated, will be removed)
# These are re-exported from the seeds submodule
from pyrit.models.storage_io import AzureBlobStorageIO, DiskStorageIO, StorageIO
from pyrit.models.strategy_result import StrategyResult, StrategyResultT

__all__ = [
    "ALLOWED_CHAT_MESSAGE_ROLES",
    "AllowedCategories",
    "AtomicAttackEvaluationIdentifier",
    "AttackResult",
    "AttackResultT",
    "AttackOutcome",
    "AudioPathDataTypeSerializer",
    "AzureBlobStorageIO",
    "BinaryPathDataTypeSerializer",
    "build_atomic_attack_identifier",
    "build_seed_identifier",
    "ChatMessage",
    "ChatMessagesDataset",
    "ChatMessageRole",
    "ChildEvalRule",
    "class_name_to_snake_case",
    "ComponentIdentifier",
    "compute_eval_hash",
    "config_hash",
    "ConversationReference",
    "ConversationStats",
    "ConversationType",
    "construct_response_from_request",
    "DataTypeSerializer",
    "data_serializer_factory",
    "DiskStorageIO",
    "EmbeddingData",
    "EmbeddingResponse",
    "EmbeddingSupport",
    "EmbeddingUsageInformation",
    "ErrorDataTypeSerializer",
    "EvaluationIdentifier",
    "get_all_harm_definitions",
    "group_conversation_message_pieces_by_sequence",
    "group_message_pieces_into_conversations",
    "HarmDefinition",
    "Identifiable",
    "IdentifierFilter",
    "IdentifierType",
    "ImagePathDataTypeSerializer",
    "Message",
    "MessagePiece",
    "NextMessageSystemPromptPaths",
    "PromptDataType",
    "PromptResponseError",
    "QuestionAnsweringDataset",
    "QuestionAnsweringEntry",
    "QuestionChoice",
    "REGISTRY_NAME_PATTERN",
    "ScaleDescription",
    "Score",
    "ScoreType",
    "ScorerEvaluationIdentifier",
    "ScenarioIdentifier",
    "ScenarioResult",
    "Seed",
    "SeedAttackGroup",
    "SeedAttackTechniqueGroup",
    "SeedObjective",
    "SeedPrompt",
    "SeedDataset",
    "SeedGroup",
    "SeedSimulatedConversation",
    "SeedType",
    "SimulatedTargetSystemPromptPaths",
    "snake_case_to_class_name",
    "sort_message_pieces",
    "StorageIO",
    "StrategyResult",
    "StrategyResultT",
    "TARGET_EVAL_PARAM_FALLBACKS",
    "TARGET_EVAL_PARAMS",
    "TextDataTypeSerializer",
    "UnvalidatedScore",
    "validate_registry_name",
    "VideoPathDataTypeSerializer",
    "RetryEvent",
]
