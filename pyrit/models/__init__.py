# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Public model exports for PyRIT core data structures and helpers.

``pyrit.models`` is the canonical data layer. Files in this package must
import only from the standard library, ``pydantic``,
``pyrit.common.deprecation``, and other ``pyrit.models.*`` submodules. The
CI test ``tests/unit/models/test_import_boundary.py`` enforces this. See
``.github/instructions/models.instructions.md`` for the rule.

Identifier types and helpers live in the ``pyrit.models.identifiers``
sub-package but are re-exported here, so callers should import them
directly from ``pyrit.models`` (e.g. ``from pyrit.models import
ComponentIdentifier``).
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.models.additional_initializer import AdditionalInitializer
    from pyrit.models.catalog import (
        ScenarioDatasetSizeCap,
        ScenarioDatasetSummary,
        ScenarioRunListItem,
        ScenarioRunSizeComponent,
        ScenarioRunSizeEstimate,
        ScenarioRunSizeEstimateRequest,
        ScenarioTechniqueSummary,
    )
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
        compute_seed_group_hash,
        config_hash,
        snake_case_to_class_name,
        validate_registry_name,
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
    from pyrit.models.messages.conversation_retry import ConversationRetry, ConversationRetryReason
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
    from pyrit.models.scenario_progress import (
        SCENARIO_RUN_PLAN_METADATA_KEY,
        SCENARIO_RUN_PLAN_VERSION,
        ScenarioAttackResultDelta,
        ScenarioProgressHeader,
        ScenarioProgressResult,
        ScenarioRunPlan,
        ScenarioRunPlanAtomicGroup,
        ScenarioRunPlanSeedGroup,
        ScenarioRunProgress,
    )
    from pyrit.models.score import (
        Condition,
        ContentEntryScorable,
        ContentScorable,
        MatchesObjective,
        MessageScorable,
        Scorable,
        ScorableUnion,
        Score,
        ScoreStatus,
        ScoreType,
        ScoringExpectation,
        UndeterminedScoreError,
        UnvalidatedScore,
        scorable_from_dict,
    )
    from pyrit.models.seeds import (
        AttackSeedGroup,
        AttackTechniqueSeedGroup,
        NextMessageSystemPromptPaths,
        Seed,
        SeedDataset,
        SeedGroup,
        SeedObjective,
        SeedPrompt,
        SeedSimulatedConversation,
        SeedUnion,
        SimulatedTargetSystemPromptPaths,
        group_seeds_into_attack_groups,
    )
    from pyrit.models.target import (
        COMMON_JSON_SCHEMAS,
        JSON_SCHEMA_METADATA_KEY,
        SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY,
        TOKEN_USAGE_METADATA_PREFIX,
        CapabilityName,
        JsonResponseConfig,
        JsonSchemaDefinition,
        TargetCapabilities,
        TokenUsage,
        get_common_json_schema,
        read_usage_int,
        read_usage_value,
        register_common_json_schema,
        unregister_common_json_schema,
    )

_LAZY_EXPORTS: dict[str, str] = {
    "ALLOWED_CHAT_MESSAGE_ROLES": "pyrit.models.messages.chat_message",
    "AdditionalInitializer": "pyrit.models.additional_initializer",
    "AtomicAttackEvaluationIdentifier": "pyrit.models.identifiers",
    "AtomicAttackIdentifier": "pyrit.models.identifiers",
    "AttackIdentifier": "pyrit.models.identifiers",
    "AttackTechniqueIdentifier": "pyrit.models.identifiers",
    "AttackResult": "pyrit.models.results.attack_result",
    "AttackResultT": "pyrit.models.results.attack_result",
    "AttackOutcome": "pyrit.models.results.attack_result",
    "ChatMessage": "pyrit.models.messages.chat_message",
    "ChatMessagesDataset": "pyrit.models.messages.chat_message",
    "ChatMessageRole": "pyrit.models.literals",
    "ChildEvalRule": "pyrit.models.identifiers",
    "class_name_to_snake_case": "pyrit.models.identifiers",
    "CapabilityName": "pyrit.models.target",
    "ComponentIdentifier": "pyrit.models.identifiers",
    "ComponentType": "pyrit.models.parameter",
    "compute_eval_hash": "pyrit.models.identifiers",
    "Condition": "pyrit.models.score",
    "config_hash": "pyrit.models.identifiers",
    "ConverterIdentifier": "pyrit.models.identifiers",
    "Conversation": "pyrit.models.messages.conversations",
    "ConversationReference": "pyrit.models.messages.conversation_reference",
    "ConversationRetry": "pyrit.models.messages.conversation_retry",
    "ConversationRetryReason": "pyrit.models.messages.conversation_retry",
    "ConversationStats": "pyrit.models.conversation_stats",
    "ConversationType": "pyrit.models.messages.conversation_reference",
    "ContentEntryScorable": "pyrit.models.score",
    "ContentScorable": "pyrit.models.score",
    "construct_response_from_request": "pyrit.models.messages.conversations",
    "display_choices": "pyrit.models.parameter",
    "EmbeddingData": "pyrit.models.embeddings",
    "EmbeddingResponse": "pyrit.models.embeddings",
    "EmbeddingSupport": "pyrit.models.embeddings",
    "EmbeddingUsageInformation": "pyrit.models.embeddings",
    "Evaluate": "pyrit.models.identifiers",
    "EvaluationIdentifier": "pyrit.models.identifiers",
    "flatten_to_message_pieces": "pyrit.models.messages.conversations",
    "get_all_harm_definitions": "pyrit.models.harm_definition",
    "get_all_values": "pyrit.models.messages.conversations",
    "group_conversation_message_pieces_by_sequence": "pyrit.models.messages.conversations",
    "group_message_pieces_into_conversations": "pyrit.models.messages.conversations",
    "group_seeds_into_attack_groups": "pyrit.models.seeds",
    "HarmDefinition": "pyrit.models.harm_definition",
    "Identifiable": "pyrit.models.identifiers",
    "IdentifierFilter": "pyrit.models.identifiers",
    "IdentifierType": "pyrit.models.identifiers",
    "JSONValue": "pyrit.models.identifiers",
    "compute_seed_group_hash": "pyrit.models.identifiers",
    "COMMON_JSON_SCHEMAS": "pyrit.models.target",
    "JsonResponseConfig": "pyrit.models.target",
    "get_common_json_schema": "pyrit.models.target",
    "register_common_json_schema": "pyrit.models.target",
    "unregister_common_json_schema": "pyrit.models.target",
    "JSON_SCHEMA_METADATA_KEY": "pyrit.models.target",
    "SEED_RESPONSE_JSON_SCHEMA_METADATA_KEY": "pyrit.models.target",
    "JsonSchemaDefinition": "pyrit.models.target",
    "MatchesObjective": "pyrit.models.score",
    "MEDIA_PATH_DATA_TYPES": "pyrit.models.literals",
    "Message": "pyrit.models.messages.message",
    "MessagePiece": "pyrit.models.messages.message_piece",
    "MessageScorable": "pyrit.models.score",
    "Modality": "pyrit.models.literals",
    "NextMessageSystemPromptPaths": "pyrit.models.seeds",
    "ObjectiveTargetEvaluationIdentifier": "pyrit.models.identifiers",
    "Parameter": "pyrit.models.parameter",
    "ParameterDestination": "pyrit.models.parameter",
    "PromptDataType": "pyrit.models.literals",
    "PromptResponseError": "pyrit.models.literals",
    "QuestionAnsweringDataset": "pyrit.models.question_answering",
    "QuestionAnsweringEntry": "pyrit.models.question_answering",
    "RegistryReference": "pyrit.models.parameter",
    "QuestionChoice": "pyrit.models.question_answering",
    "REGISTRY_NAME_PATTERN": "pyrit.models.identifiers",
    "ScaleDescription": "pyrit.models.harm_definition",
    "Scorable": "pyrit.models.score",
    "ScorableUnion": "pyrit.models.score",
    "Score": "pyrit.models.score",
    "ScoreStatus": "pyrit.models.score",
    "ScoreType": "pyrit.models.score",
    "ScoringExpectation": "pyrit.models.score",
    "ScenarioEvaluationIdentifier": "pyrit.models.identifiers",
    "ScorerEvaluationIdentifier": "pyrit.models.identifiers",
    "ScorerIdentifier": "pyrit.models.identifiers",
    "ScenarioIdentifier": "pyrit.models.identifiers",
    "ScenarioDatasetSizeCap": "pyrit.models.catalog",
    "ScenarioDatasetSummary": "pyrit.models.catalog",
    "ScenarioRunListItem": "pyrit.models.catalog",
    "ScenarioRunSizeComponent": "pyrit.models.catalog",
    "ScenarioRunSizeEstimate": "pyrit.models.catalog",
    "ScenarioRunSizeEstimateRequest": "pyrit.models.catalog",
    "ScenarioTechniqueSummary": "pyrit.models.catalog",
    "ScenarioResult": "pyrit.models.results.scenario_result",
    "ScenarioRunState": "pyrit.models.results.scenario_result",
    "SCENARIO_RUN_PLAN_METADATA_KEY": "pyrit.models.scenario_progress",
    "SCENARIO_RUN_PLAN_VERSION": "pyrit.models.scenario_progress",
    "ScenarioAttackResultDelta": "pyrit.models.scenario_progress",
    "ScenarioProgressHeader": "pyrit.models.scenario_progress",
    "ScenarioProgressResult": "pyrit.models.scenario_progress",
    "ScenarioRunPlan": "pyrit.models.scenario_progress",
    "ScenarioRunPlanAtomicGroup": "pyrit.models.scenario_progress",
    "ScenarioRunPlanSeedGroup": "pyrit.models.scenario_progress",
    "ScenarioRunProgress": "pyrit.models.scenario_progress",
    "Seed": "pyrit.models.seeds",
    "AttackSeedGroup": "pyrit.models.seeds",
    "AttackTechniqueSeedGroup": "pyrit.models.seeds",
    "SeedObjective": "pyrit.models.seeds",
    "SeedPrompt": "pyrit.models.seeds",
    "SeedDataset": "pyrit.models.seeds",
    "SeedGroup": "pyrit.models.seeds",
    "SeedIdentifier": "pyrit.models.identifiers",
    "SeedSimulatedConversation": "pyrit.models.seeds",
    "SeedType": "pyrit.models.literals",
    "SeedUnion": "pyrit.models.seeds",
    "SimulatedTargetSystemPromptPaths": "pyrit.models.seeds",
    "snake_case_to_class_name": "pyrit.models.identifiers",
    "sort_message_pieces": "pyrit.models.messages.message_piece",
    "StrategyResult": "pyrit.models.results.strategy_result",
    "StrategyResultT": "pyrit.models.results.strategy_result",
    "TARGET_EVAL_PARAM_FALLBACKS": "pyrit.models.identifiers",
    "TARGET_EVAL_PARAMS": "pyrit.models.identifiers",
    "TargetCapabilities": "pyrit.models.target",
    "TargetIdentifier": "pyrit.models.identifiers",
    "TOKEN_USAGE_METADATA_PREFIX": "pyrit.models.target",
    "TokenUsage": "pyrit.models.target",
    "ToolCall": "pyrit.models.messages.chat_message",
    "UndeterminedScoreError": "pyrit.models.score",
    "UnvalidatedScore": "pyrit.models.score",
    "read_usage_int": "pyrit.models.target",
    "read_usage_value": "pyrit.models.target",
    "scorable_from_dict": "pyrit.models.score",
    "validate_registry_name": "pyrit.models.identifiers",
    "RetryEvent": "pyrit.models.retry_event",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    """
    Resolve a public model export on first access.

    Args:
        name (str): The requested public name.

    Returns:
        object: The resolved export.
    """
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    """Return package attributes, including unresolved exports."""
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
