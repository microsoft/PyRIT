# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The enums migrated from ``(str, Enum)`` to ``StrEnum`` in #4508 format as their value."""

import importlib
from enum import StrEnum

import pytest

_MIGRATED_ENUMS: dict[str, tuple[str, ...]] = {
    "pyrit.analytics.paired_outcome_statistics": ("PairedOutcomeTransition",),
    "pyrit.backend.models.operations": (
        "EvidenceAvailability",
        "FindingExportFormat",
    ),
    "pyrit.backend.services.media_persistence": ("MediaOrigin",),
    "pyrit.cli._cli_args": ("ScenarioResultView",),
    "pyrit.common.tool_configs": ("OpenAIToolType",),
    "pyrit.converter.puzzled.puzzle_builders": ("PuzzleType",),
    "pyrit.datasets.seed_datasets.remote.xl_safety_bench_dataset": (
        "XLSafetyBenchCountry",
        "XLSafetyBenchJailbreakCategory",
        "XLSafetyBenchCulturalCategory",
        "XLSafetyBenchLanguageMode",
    ),
    "pyrit.executor.attack.compound.sequential_attack": ("SequenceCompletionPolicy",),
    "pyrit.executor.attack.core.attack_preparation": ("AttackPreparationFailureKind",),
    "pyrit.executor.promptgen.gcg.attack.base.attack_manager": (
        "StopReason",
        "ModelWorkerOperation",
    ),
    "pyrit.models.analytics": (
        "AttackResultSelection",
        "AttackAnalyticsDimensionName",
        "AttackAnalyticsConverterDirection",
        "AttackAnalyticsMatchMode",
        "AttackAnalyticsValueKind",
    ),
    "pyrit.models.catalog.registry_recipe": (
        "RegistryEntryOrigin",
        "RegistryRecipeKind",
        "RestoreFailureReason",
    ),
    "pyrit.models.catalog.scenario": (
        "ScenarioRunSizeEstimateStatus",
        "ScenarioRunSizeEstimateCondition",
    ),
    "pyrit.models.finding": ("_FindingSeverity",),
    "pyrit.models.literals": ("Modality",),
    "pyrit.models.parameter": ("ParameterDestination",),
    "pyrit.models.prompt_generation": ("PromptGenerationStatus",),
    "pyrit.models.results.attack_result": ("AttackResultRole",),
    "pyrit.models.scenario_dataset": (
        "DatasetFetchPolicy",
        "DatasetSelectionMode",
    ),
    "pyrit.models.scenario_dataset_size_estimate": (
        "ScenarioDatasetSizeEstimateKind",
        "DatasetLimitState",
    ),
    "pyrit.models.scenario_progress": (
        "ScenarioRunPlanGroupKind",
        "ScenarioObjectiveScorerAttribution",
        "ScenarioObjectiveScorerScope",
    ),
    "pyrit.models.scenario_suite_execution": (
        "ScenarioSuiteExecutionStatus",
        "ScenarioSuiteSubmissionState",
    ),
    "pyrit.models.score.assert_judgment": (
        "AssertSkipReason",
        "AssertSampleVote",
    ),
    "pyrit.models.score.observation": ("Acquisition",),
    "pyrit.models.score.score": ("ScoreStatus",),
    "pyrit.models.score.scorer_capabilities": ("ScorerCapabilityName",),
    "pyrit.models.score.surface": ("SurfaceMatch",),
    "pyrit.models.score.trace": (
        "TraceSpanStatus",
        "ExecutionStepKind",
    ),
    "pyrit.models.scorer_selection_report": (
        "ScorerMismatchKind",
        "ScorerVerdictStatus",
    ),
    "pyrit.models.seeds.assert_generation": (
        "AssertSamplingMethod",
        "AssertReplication",
        "AssertPatternRole",
    ),
    "pyrit.models.target.target_capabilities": ("CapabilityName",),
    "pyrit.models.target.tool_execution_metadata": (
        "ToolLoopStopReason",
        "ToolCaptureGap",
    ),
    "pyrit.models.target.web_search_config": ("WebSearchMode",),
    "pyrit.output.finding_report.report": ("FindingReportColumn",),
    "pyrit.prompt_target.common.target_capabilities": ("UnsupportedCapabilityBehavior",),
    "pyrit.prompt_target.openai.openai_response_target": ("MessagePieceType",),
    "pyrit.scenario.core._attack_constructor_compatibility": ("ScorerOverridePolicy",),
    "pyrit.scenario.core.technique_requirements": ("IncompatibleTechniquePolicy",),
    "pyrit.score.evidence.conversation_trace_evidence": ("TraceEvidenceRequirement",),
    "pyrit.score.float_scale.roblox_pii_scorer": ("RobloxPiiCategory",),
    "pyrit.score.true_false.assert_policy_scorer": (
        "AssertScoringDimension",
        "AssertJudgmentMode",
    ),
    "pyrit.setup.initializers.targets": ("TargetInitializerTags",),
    "pyrit.setup.initializers.techniques.technique_initializer": ("TechniqueInitializerTags",),
}


@pytest.mark.parametrize(
    ("module_name", "class_name"),
    [(module_name, class_name) for module_name, class_names in _MIGRATED_ENUMS.items() for class_name in class_names],
)
def test_migrated_enum_formats_as_its_value(module_name: str, class_name: str) -> None:
    if module_name.startswith("pyrit.executor.promptgen.gcg"):
        pytest.importorskip("torch", reason="GCG modules need torch")

    enum_class = getattr(importlib.import_module(module_name), class_name)

    assert issubclass(enum_class, StrEnum)
    for member in enum_class:
        assert str(member) == f"{member}" == member.value
        assert enum_class(member.value) is member
