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

import importlib
from typing import Any

from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.conversation_stats import ConversationStats
from pyrit.models.embeddings import EmbeddingData, EmbeddingResponse, EmbeddingSupport, EmbeddingUsageInformation
from pyrit.models.harm_definition import HarmDefinition, ScaleDescription, get_all_harm_definitions
from pyrit.models.identifiers import (
    REGISTRY_NAME_PATTERN,
    TARGET_EVAL_PARAM_FALLBACKS,
    TARGET_EVAL_PARAMS,
    AtomicAttackEvaluationIdentifier,
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackTechniqueIdentifier,
    ChildEvalRule,
    ComponentIdentifier,
    ConverterIdentifier,
    Evaluate,
    EvaluationIdentifier,
    Identifiable,
    IdentifierFilter,
    IdentifierType,
    JSONValue,
    ObjectiveTargetEvaluationIdentifier,
    ScenarioEvaluationIdentifier,
    ScenarioIdentifier,
    ScorerEvaluationIdentifier,
    ScorerIdentifier,
    SeedIdentifier,
    TargetIdentifier,
    class_name_to_snake_case,
    compute_eval_hash,
    config_hash,
    snake_case_to_class_name,
    validate_registry_name,
)
from pyrit.models.json_schema_definition import (
    COMMON_JSON_SCHEMAS,
    JSON_SCHEMA_METADATA_KEY,
    SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY,
    JsonSchemaDefinition,
    get_common_json_schema,
    register_common_json_schema,
    unregister_common_json_schema,
)
from pyrit.models.literals import (
    MEDIA_PATH_DATA_TYPES,
    ChatMessageRole,
    Modality,
    PromptDataType,
    PromptResponseError,
    SeedType,
)
from pyrit.models.messages import (
    Conversation,
    Message,
    MessagePiece,
    construct_response_from_request,
    flatten_to_message_pieces,
    get_all_values,
    group_conversation_message_pieces_by_sequence,
    group_message_pieces_into_conversations,
    sort_message_pieces,
)
from pyrit.models.messages.chat_message import (
    ALLOWED_CHAT_MESSAGE_ROLES,
    ChatMessage,
    ChatMessagesDataset,
    ToolCall,
)
from pyrit.models.messages.conversation_reference import ConversationReference, ConversationType
from pyrit.models.parameter import (
    ComponentType,
    Parameter,
    ParameterDestination,
    RegistryReference,
    display_choices,
)
from pyrit.models.question_answering import QuestionAnsweringDataset, QuestionAnsweringEntry, QuestionChoice
from pyrit.models.results.attack_result import AttackOutcome, AttackResult, AttackResultT
from pyrit.models.results.scenario_result import ScenarioResult, ScenarioRunState
from pyrit.models.results.strategy_result import StrategyResult, StrategyResultT
from pyrit.models.retry_event import RetryEvent
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
    SeedUnion,
    SimulatedTargetSystemPromptPaths,
    group_seeds_into_attack_groups,
)
from pyrit.models.target_capabilities import CapabilityName, TargetCapabilities

__all__ = [
    "ALLOWED_CHAT_MESSAGE_ROLES",
    "AllowedCategories",
    "AtomicAttackEvaluationIdentifier",
    "AtomicAttackIdentifier",
    "AttackIdentifier",
    "AttackTechniqueIdentifier",
    "AttackResult",
    "AttackResultT",
    "AttackOutcome",
    "AudioPathDataTypeSerializer",
    "AzureBlobStorageIO",
    "BinaryPathDataTypeSerializer",
    "ChatMessage",
    "ChatMessagesDataset",
    "ChatMessageRole",
    "ChildEvalRule",
    "class_name_to_snake_case",
    "CapabilityName",
    "ComponentIdentifier",
    "ComponentType",
    "compute_eval_hash",
    "config_hash",
    "ConverterIdentifier",
    "Conversation",
    "ConversationReference",
    "ConversationStats",
    "ConversationType",
    "construct_response_from_request",
    "DataTypeSerializer",
    "data_serializer_factory",
    "display_choices",
    "DiskStorageIO",
    "EmbeddingData",
    "EmbeddingResponse",
    "EmbeddingSupport",
    "EmbeddingUsageInformation",
    "ErrorDataTypeSerializer",
    "Evaluate",
    "EvaluationIdentifier",
    "flatten_to_message_pieces",
    "get_all_harm_definitions",
    "get_all_values",
    "group_conversation_message_pieces_by_sequence",
    "group_message_pieces_into_conversations",
    "group_seeds_into_attack_groups",
    "HarmDefinition",
    "Identifiable",
    "IdentifierFilter",
    "IdentifierType",
    "ImagePathDataTypeSerializer",
    "JSONValue",
    "COMMON_JSON_SCHEMAS",
    "get_common_json_schema",
    "register_common_json_schema",
    "unregister_common_json_schema",
    "JSON_SCHEMA_METADATA_KEY",
    "SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY",
    "JsonSchemaDefinition",
    "MEDIA_PATH_DATA_TYPES",
    "Message",
    "MessagePiece",
    "Modality",
    "NextMessageSystemPromptPaths",
    "ObjectiveTargetEvaluationIdentifier",
    "Parameter",
    "ParameterDestination",
    "PromptDataType",
    "PromptResponseError",
    "QuestionAnsweringDataset",
    "QuestionAnsweringEntry",
    "RegistryReference",
    "QuestionChoice",
    "REGISTRY_NAME_PATTERN",
    "ScaleDescription",
    "Score",
    "ScoreType",
    "ScenarioEvaluationIdentifier",
    "ScorerEvaluationIdentifier",
    "ScorerIdentifier",
    "ScenarioIdentifier",
    "ScenarioResult",
    "ScenarioRunState",
    "Seed",
    "SeedAttackGroup",
    "SeedAttackTechniqueGroup",
    "SeedObjective",
    "SeedPrompt",
    "SeedDataset",
    "SeedGroup",
    "SeedIdentifier",
    "SeedSimulatedConversation",
    "SeedType",
    "SeedUnion",
    "SimulatedTargetSystemPromptPaths",
    "snake_case_to_class_name",
    "sort_message_pieces",
    "StorageIO",
    "StrategyResult",
    "StrategyResultT",
    "TARGET_EVAL_PARAM_FALLBACKS",
    "TARGET_EVAL_PARAMS",
    "TargetCapabilities",
    "TargetIdentifier",
    "TextDataTypeSerializer",
    "ToolCall",
    "UnvalidatedScore",
    "validate_registry_name",
    "VideoPathDataTypeSerializer",
    "RetryEvent",
]

# Names that moved to ``pyrit.memory.storage``. Served lazily via importlib so that
# importing ``pyrit.models`` stays import-boundary clean and fires no warning until a
# moved name is actually accessed. Will be removed in 0.17.0.
_MOVED_TO_MEMORY_STORAGE: dict[str, str] = {
    "AllowedCategories": "pyrit.memory.storage.serializers",
    "AudioPathDataTypeSerializer": "pyrit.memory.storage.serializers",
    "BinaryPathDataTypeSerializer": "pyrit.memory.storage.serializers",
    "DataTypeSerializer": "pyrit.memory.storage.serializers",
    "ErrorDataTypeSerializer": "pyrit.memory.storage.serializers",
    "ImagePathDataTypeSerializer": "pyrit.memory.storage.serializers",
    "TextDataTypeSerializer": "pyrit.memory.storage.serializers",
    "VideoPathDataTypeSerializer": "pyrit.memory.storage.serializers",
    "data_serializer_factory": "pyrit.memory.storage.serializers",
    "AzureBlobStorageIO": "pyrit.memory.storage.storage",
    "DiskStorageIO": "pyrit.memory.storage.storage",
    "StorageIO": "pyrit.memory.storage.storage",
}

_warned: set[str] = set()


def __getattr__(name: str) -> Any:
    if name in _MOVED_TO_MEMORY_STORAGE:
        target_module = _MOVED_TO_MEMORY_STORAGE[name]
        if name not in _warned:
            print_deprecation_message(
                old_item=f"{__name__}.{name}",
                new_item=f"{target_module}.{name}",
                removed_in="0.17.0",
            )
            _warned.add(name)
        return getattr(importlib.import_module(target_module), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
