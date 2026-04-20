# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models.attack_result import AttackOutcome, AttackResult, AttackResultT
from pyrit.models.chat_message import (
    ALLOWED_CHAT_MESSAGE_ROLES,
    ChatMessage,
    ChatMessageListDictContent,
    ChatMessagesDataset,
)
from pyrit.models.conversation_reference import ConversationReference, ConversationType
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
from pyrit.models.literals import ChatMessageRole, PromptDataType, PromptResponseError, SeedType
from pyrit.models.message import (
    Message,
    construct_response_from_request,
    group_conversation_message_pieces_by_sequence,
    group_message_pieces_into_conversations,
)
from pyrit.models.message_piece import MessagePiece, sort_message_pieces
from pyrit.models.question_answering import QuestionAnsweringDataset, QuestionAnsweringEntry, QuestionChoice
from pyrit.models.scenario_result import ScenarioIdentifier, ScenarioResult
from pyrit.models.score import Score, ScoreType, UnvalidatedScore

# Seeds - import from new seeds submodule for forward compatibility
# Also keep imports from old locations for backward compatibility
from pyrit.models.seeds import (
    NextMessageSystemPromptPaths,
    Seed,
    SeedAttackGroup,
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
    "AttackResult",
    "AttackResultT",
    "AttackOutcome",
    "AudioPathDataTypeSerializer",
    "AzureBlobStorageIO",
    "BinaryPathDataTypeSerializer",
    "ChatMessage",
    "ChatMessagesDataset",
    "ChatMessageRole",
    "ChatMessageListDictContent",
    "ConversationReference",
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
    "get_all_harm_definitions",
    "group_conversation_message_pieces_by_sequence",
    "group_message_pieces_into_conversations",
    "HarmDefinition",
    "ImagePathDataTypeSerializer",
    "Message",
    "MessagePiece",
    "NextMessageSystemPromptPaths",
    "PromptDataType",
    "PromptResponseError",
    "QuestionAnsweringDataset",
    "QuestionAnsweringEntry",
    "QuestionChoice",
    "ScaleDescription",
    "Score",
    "ScoreType",
    "ScenarioIdentifier",
    "ScenarioResult",
    "Seed",
    "SeedAttackGroup",
    "SeedObjective",
    "SeedPrompt",
    "SeedDataset",
    "SeedGroup",
    "SeedSimulatedConversation",
    "SeedType",
    "SimulatedTargetSystemPromptPaths",
    "sort_message_pieces",
    "StorageIO",
    "StrategyResult",
    "StrategyResultT",
    "TextDataTypeSerializer",
    "UnvalidatedScore",
    "VideoPathDataTypeSerializer",
]
