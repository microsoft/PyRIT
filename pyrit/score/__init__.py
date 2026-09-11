# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Scoring functionality for evaluating AI model responses across various dimensions
including harm detection, objective completion, and content classification.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.output.scorer.base import ScorerPrinterBase as ScorerPrinter
    from pyrit.score.batch_scorer import BatchScorer
    from pyrit.score.conversation_scorer import ConversationScorer, create_conversation_scorer
    from pyrit.score.float_scale.audio_float_scale_scorer import AudioFloatScaleScorer
    from pyrit.score.float_scale.azure_content_filter_scorer import AzureContentFilterScorer
    from pyrit.score.float_scale.float_scale_score_aggregator import (
        FloatScaleScoreAggregator,
        FloatScaleScorerAllCategories,
        FloatScaleScorerByCategory,
    )
    from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer, MessageFloatScaleScorer
    from pyrit.score.float_scale.insecure_code_scorer import InsecureCodeScorer, render_insecure_code_system_prompt
    from pyrit.score.float_scale.likert_scale import LikertScale, LikertScaleEntry
    from pyrit.score.float_scale.numeric_scale import NumericRange, NumericRubric
    from pyrit.score.float_scale.plagiarism_scorer import PlagiarismMetric, PlagiarismScorer
    from pyrit.score.float_scale.roblox_pii_scorer import RobloxPiiCategory, RobloxPiiScorer
    from pyrit.score.float_scale.self_ask_general_float_scale_scorer import SelfAskGeneralFloatScaleScorer
    from pyrit.score.float_scale.self_ask_likert_scorer import (
        LikertScaleEvalFiles,
        LikertScalePaths,
        SelfAskLikertScorer,
        render_likert_system_prompt,
    )
    from pyrit.score.float_scale.self_ask_scale_scorer import SelfAskScaleScorer, render_scale_system_prompt
    from pyrit.score.float_scale.system_prompt_extraction_scorer import SystemPromptExtractionScorer
    from pyrit.score.float_scale.video_float_scale_scorer import VideoFloatScaleScorer
    from pyrit.score.message_scorable_resolver import MessageScorableResolver
    from pyrit.score.message_scorer import MessageScorer
    from pyrit.score.response_handler import CallableResponseHandler, JsonSchemaResponseHandler, ResponseHandler
    from pyrit.score.scorable import ContentScorable, MessageScorable, Scorable
    from pyrit.score.scorer import Scorer
    from pyrit.score.scorer_evaluation.human_labeled_dataset import (
        HarmHumanLabeledEntry,
        HumanLabeledDataset,
        HumanLabeledEntry,
        ObjectiveHumanLabeledEntry,
    )
    from pyrit.score.scorer_evaluation.metrics_type import MetricsType, RegistryUpdateBehavior
    from pyrit.score.scorer_evaluation.scorer_evaluator import (
        HarmScorerEvaluator,
        ObjectiveScorerEvaluator,
        ScorerEvalDatasetFiles,
        ScorerEvaluator,
    )
    from pyrit.score.scorer_evaluation.scorer_metrics import (
        HarmScorerMetrics,
        ObjectiveScorerMetrics,
        ScorerMetrics,
        ScorerMetricsWithIdentity,
    )
    from pyrit.score.scorer_evaluation.scorer_metrics_io import (
        find_objective_metrics_by_eval_hash,
        get_all_harm_metrics,
        get_all_objective_metrics,
    )
    from pyrit.score.scorer_info import get_scorer_info
    from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
    from pyrit.score.true_false.audio_true_false_scorer import AudioTrueFalseScorer
    from pyrit.score.true_false.decoding_scorer import DecodingScorer
    from pyrit.score.true_false.float_scale_threshold_scorer import FloatScaleThresholdScorer
    from pyrit.score.true_false.gandalf_scorer import GandalfScorer
    from pyrit.score.true_false.llamaguard_parser import LLAMAGUARD_3_CATEGORY_CODES, parse_llamaguard_response
    from pyrit.score.true_false.llamaguard_policy import LlamaGuardCategory, LlamaGuardPolicy
    from pyrit.score.true_false.llamaguard_scorer import (
        LlamaGuardMessageRole,
        LlamaGuardScorer,
        render_llamaguard_prompt,
    )
    from pyrit.score.true_false.prompt_shield_scorer import PromptShieldScorer
    from pyrit.score.true_false.question_answer_scorer import QuestionAnswerScorer
    from pyrit.score.true_false.regex.anthrax_keyword_scorer import AnthraxKeywordScorer
    from pyrit.score.true_false.regex.credential_leak_scorer import CredentialLeakScorer
    from pyrit.score.true_false.regex.fentanyl_keyword_scorer import FentanylKeywordScorer
    from pyrit.score.true_false.regex.ldap_injection_output_scorer import LDAPInjectionOutputScorer
    from pyrit.score.true_false.regex.markdown_injection import MarkdownInjectionScorer
    from pyrit.score.true_false.regex.meth_keyword_scorer import MethKeywordScorer
    from pyrit.score.true_false.regex.nerve_agent_keyword_scorer import NerveAgentKeywordScorer
    from pyrit.score.true_false.regex.open_redirect_output_scorer import OpenRedirectOutputScorer
    from pyrit.score.true_false.regex.package_hallucination_scorer import PackageEcosystem, PackageHallucinationScorer
    from pyrit.score.true_false.regex.path_traversal_output_scorer import PathTraversalOutputScorer
    from pyrit.score.true_false.regex.regex_scorer import RegexScorer
    from pyrit.score.true_false.regex.shell_command_output_scorer import ShellCommandOutputScorer
    from pyrit.score.true_false.regex.sql_injection_output_scorer import SQLInjectionOutputScorer
    from pyrit.score.true_false.regex.ssrf_output_scorer import SSRFOutputScorer
    from pyrit.score.true_false.regex.ssti_output_scorer import SSTIOutputScorer
    from pyrit.score.true_false.regex.static_prompt_injection_scorer import StaticPromptInjectionScorer
    from pyrit.score.true_false.regex.xss_output_scorer import XSSOutputScorer
    from pyrit.score.true_false.regex.xxe_output_scorer import XXEOutputScorer
    from pyrit.score.true_false.self_ask_category_scorer import (
        ContentClassifier,
        ContentClassifierCategory,
        ContentClassifierPaths,
        SelfAskCategoryScorer,
        render_category_system_prompt,
    )
    from pyrit.score.true_false.self_ask_general_true_false_scorer import SelfAskGeneralTrueFalseScorer
    from pyrit.score.true_false.self_ask_question_answer_scorer import SelfAskQuestionAnswerScorer
    from pyrit.score.true_false.self_ask_refusal_scorer import RefusalScorerPaths, SelfAskRefusalScorer
    from pyrit.score.true_false.self_ask_true_false_scorer import (
        SelfAskTrueFalseScorer,
        TrueFalseQuestion,
        TrueFalseQuestionPaths,
        render_true_false_system_prompt,
    )
    from pyrit.score.true_false.shieldgemma_parser import parse_shieldgemma_response
    from pyrit.score.true_false.shieldgemma_policy import (
        SHIELDGEMMA_DEFAULT_POLICY_PATH,
        ShieldGemmaGuideline,
        ShieldGemmaMessageRole,
        ShieldGemmaPolicy,
    )
    from pyrit.score.true_false.shieldgemma_scorer import ShieldGemmaScorer, render_shieldgemma_prompt
    from pyrit.score.true_false.substring_scorer import SubStringScorer
    from pyrit.score.true_false.true_false_composite_scorer import TrueFalseCompositeScorer
    from pyrit.score.true_false.true_false_inverter_scorer import TrueFalseInverterScorer
    from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc, TrueFalseScoreAggregator
    from pyrit.score.true_false.true_false_scorer import MessageTrueFalseScorer, TrueFalseScorer
    from pyrit.score.true_false.video_true_false_scorer import VideoTrueFalseScorer

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AnthraxKeywordScorer": "pyrit.score.true_false.regex.anthrax_keyword_scorer",
    "AudioFloatScaleScorer": "pyrit.score.float_scale.audio_float_scale_scorer",
    "AudioTrueFalseScorer": "pyrit.score.true_false.audio_true_false_scorer",
    "AzureContentFilterScorer": "pyrit.score.float_scale.azure_content_filter_scorer",
    "BatchScorer": "pyrit.score.batch_scorer",
    "CallableResponseHandler": "pyrit.score.response_handler",
    "ContentScorable": "pyrit.score.scorable",
    "ContentClassifier": "pyrit.score.true_false.self_ask_category_scorer",
    "ContentClassifierCategory": "pyrit.score.true_false.self_ask_category_scorer",
    "ContentClassifierPaths": "pyrit.score.true_false.self_ask_category_scorer",
    "ConversationScorer": "pyrit.score.conversation_scorer",
    "CredentialLeakScorer": "pyrit.score.true_false.regex.credential_leak_scorer",
    "DecodingScorer": "pyrit.score.true_false.decoding_scorer",
    "FentanylKeywordScorer": "pyrit.score.true_false.regex.fentanyl_keyword_scorer",
    "create_conversation_scorer": "pyrit.score.conversation_scorer",
    "FloatScaleScoreAggregator": "pyrit.score.float_scale.float_scale_score_aggregator",
    "FloatScaleScorerAllCategories": "pyrit.score.float_scale.float_scale_score_aggregator",
    "FloatScaleScorerByCategory": "pyrit.score.float_scale.float_scale_score_aggregator",
    "FloatScaleScorer": "pyrit.score.float_scale.float_scale_scorer",
    "MessageFloatScaleScorer": "pyrit.score.float_scale.float_scale_scorer",
    "MessageTrueFalseScorer": "pyrit.score.true_false.true_false_scorer",
    "FloatScaleThresholdScorer": "pyrit.score.true_false.float_scale_threshold_scorer",
    "GandalfScorer": "pyrit.score.true_false.gandalf_scorer",
    "HarmHumanLabeledEntry": "pyrit.score.scorer_evaluation.human_labeled_dataset",
    "HarmScorerEvaluator": "pyrit.score.scorer_evaluation.scorer_evaluator",
    "HarmScorerMetrics": "pyrit.score.scorer_evaluation.scorer_metrics",
    "HumanLabeledDataset": "pyrit.score.scorer_evaluation.human_labeled_dataset",
    "HumanLabeledEntry": "pyrit.score.scorer_evaluation.human_labeled_dataset",
    "InsecureCodeScorer": "pyrit.score.float_scale.insecure_code_scorer",
    "JsonSchemaResponseHandler": "pyrit.score.response_handler",
    "LDAPInjectionOutputScorer": "pyrit.score.true_false.regex.ldap_injection_output_scorer",
    "LikertScaleEvalFiles": "pyrit.score.float_scale.self_ask_likert_scorer",
    "LikertScale": "pyrit.score.float_scale.likert_scale",
    "LikertScaleEntry": "pyrit.score.float_scale.likert_scale",
    "LikertScalePaths": "pyrit.score.float_scale.self_ask_likert_scorer",
    "LLAMAGUARD_3_CATEGORY_CODES": "pyrit.score.true_false.llamaguard_parser",
    "LlamaGuardCategory": "pyrit.score.true_false.llamaguard_policy",
    "LlamaGuardMessageRole": "pyrit.score.true_false.llamaguard_scorer",
    "LlamaGuardPolicy": "pyrit.score.true_false.llamaguard_policy",
    "LlamaGuardScorer": "pyrit.score.true_false.llamaguard_scorer",
    "MarkdownInjectionScorer": "pyrit.score.true_false.regex.markdown_injection",
    "MessageScorableResolver": "pyrit.score.message_scorable_resolver",
    "MessageScorable": "pyrit.score.scorable",
    "MessageScorer": "pyrit.score.message_scorer",
    "MethKeywordScorer": "pyrit.score.true_false.regex.meth_keyword_scorer",
    "MetricsType": "pyrit.score.scorer_evaluation.metrics_type",
    "NerveAgentKeywordScorer": "pyrit.score.true_false.regex.nerve_agent_keyword_scorer",
    "NumericRange": "pyrit.score.float_scale.numeric_scale",
    "NumericRubric": "pyrit.score.float_scale.numeric_scale",
    "ObjectiveHumanLabeledEntry": "pyrit.score.scorer_evaluation.human_labeled_dataset",
    "ObjectiveScorerEvaluator": "pyrit.score.scorer_evaluation.scorer_evaluator",
    "ObjectiveScorerMetrics": "pyrit.score.scorer_evaluation.scorer_metrics",
    "OpenRedirectOutputScorer": "pyrit.score.true_false.regex.open_redirect_output_scorer",
    "PackageEcosystem": "pyrit.score.true_false.regex.package_hallucination_scorer",
    "PackageHallucinationScorer": "pyrit.score.true_false.regex.package_hallucination_scorer",
    "parse_llamaguard_response": "pyrit.score.true_false.llamaguard_parser",
    "parse_shieldgemma_response": "pyrit.score.true_false.shieldgemma_parser",
    "PathTraversalOutputScorer": "pyrit.score.true_false.regex.path_traversal_output_scorer",
    "PlagiarismMetric": "pyrit.score.float_scale.plagiarism_scorer",
    "PlagiarismScorer": "pyrit.score.float_scale.plagiarism_scorer",
    "PromptShieldScorer": "pyrit.score.true_false.prompt_shield_scorer",
    "QuestionAnswerScorer": "pyrit.score.true_false.question_answer_scorer",
    "RegexScorer": "pyrit.score.true_false.regex.regex_scorer",
    "RegistryUpdateBehavior": "pyrit.score.scorer_evaluation.metrics_type",
    "render_category_system_prompt": "pyrit.score.true_false.self_ask_category_scorer",
    "render_insecure_code_system_prompt": "pyrit.score.float_scale.insecure_code_scorer",
    "render_llamaguard_prompt": "pyrit.score.true_false.llamaguard_scorer",
    "render_likert_system_prompt": "pyrit.score.float_scale.self_ask_likert_scorer",
    "render_scale_system_prompt": "pyrit.score.float_scale.self_ask_scale_scorer",
    "render_shieldgemma_prompt": "pyrit.score.true_false.shieldgemma_scorer",
    "render_true_false_system_prompt": "pyrit.score.true_false.self_ask_true_false_scorer",
    "ResponseHandler": "pyrit.score.response_handler",
    "RobloxPiiCategory": "pyrit.score.float_scale.roblox_pii_scorer",
    "RobloxPiiScorer": "pyrit.score.float_scale.roblox_pii_scorer",
    "Scorer": "pyrit.score.scorer",
    "Scorable": "pyrit.score.scorable",
    "ScorerEvalDatasetFiles": "pyrit.score.scorer_evaluation.scorer_evaluator",
    "ScorerEvaluator": "pyrit.score.scorer_evaluation.scorer_evaluator",
    "ScorerMetrics": "pyrit.score.scorer_evaluation.scorer_metrics",
    "ScorerMetricsWithIdentity": "pyrit.score.scorer_evaluation.scorer_metrics",
    "get_all_harm_metrics": "pyrit.score.scorer_evaluation.scorer_metrics_io",
    "get_all_objective_metrics": "pyrit.score.scorer_evaluation.scorer_metrics_io",
    "get_scorer_info": "pyrit.score.scorer_info",
    "find_objective_metrics_by_eval_hash": "pyrit.score.scorer_evaluation.scorer_metrics_io",
    "ScorerPromptValidator": "pyrit.score.scorer_prompt_validator",
    "SelfAskCategoryScorer": "pyrit.score.true_false.self_ask_category_scorer",
    "SelfAskGeneralFloatScaleScorer": "pyrit.score.float_scale.self_ask_general_float_scale_scorer",
    "SelfAskGeneralTrueFalseScorer": "pyrit.score.true_false.self_ask_general_true_false_scorer",
    "SelfAskLikertScorer": "pyrit.score.float_scale.self_ask_likert_scorer",
    "SelfAskQuestionAnswerScorer": "pyrit.score.true_false.self_ask_question_answer_scorer",
    "RefusalScorerPaths": "pyrit.score.true_false.self_ask_refusal_scorer",
    "SelfAskRefusalScorer": "pyrit.score.true_false.self_ask_refusal_scorer",
    "SelfAskScaleScorer": "pyrit.score.float_scale.self_ask_scale_scorer",
    "SelfAskTrueFalseScorer": "pyrit.score.true_false.self_ask_true_false_scorer",
    "ScorerPrinter": ("pyrit.output.scorer.base", "ScorerPrinterBase"),
    "SHIELDGEMMA_DEFAULT_POLICY_PATH": "pyrit.score.true_false.shieldgemma_policy",
    "ShieldGemmaGuideline": "pyrit.score.true_false.shieldgemma_policy",
    "ShieldGemmaMessageRole": "pyrit.score.true_false.shieldgemma_policy",
    "ShieldGemmaPolicy": "pyrit.score.true_false.shieldgemma_policy",
    "ShieldGemmaScorer": "pyrit.score.true_false.shieldgemma_scorer",
    "ShellCommandOutputScorer": "pyrit.score.true_false.regex.shell_command_output_scorer",
    "SQLInjectionOutputScorer": "pyrit.score.true_false.regex.sql_injection_output_scorer",
    "SSRFOutputScorer": "pyrit.score.true_false.regex.ssrf_output_scorer",
    "SSTIOutputScorer": "pyrit.score.true_false.regex.ssti_output_scorer",
    "StaticPromptInjectionScorer": "pyrit.score.true_false.regex.static_prompt_injection_scorer",
    "SubStringScorer": "pyrit.score.true_false.substring_scorer",
    "SystemPromptExtractionScorer": "pyrit.score.float_scale.system_prompt_extraction_scorer",
    "TrueFalseCompositeScorer": "pyrit.score.true_false.true_false_composite_scorer",
    "TrueFalseInverterScorer": "pyrit.score.true_false.true_false_inverter_scorer",
    "TrueFalseQuestion": "pyrit.score.true_false.self_ask_true_false_scorer",
    "TrueFalseQuestionPaths": "pyrit.score.true_false.self_ask_true_false_scorer",
    "TrueFalseScoreAggregator": "pyrit.score.true_false.true_false_score_aggregator",
    "TrueFalseAggregatorFunc": "pyrit.score.true_false.true_false_score_aggregator",
    "TrueFalseScorer": "pyrit.score.true_false.true_false_scorer",
    "VideoFloatScaleScorer": "pyrit.score.float_scale.video_float_scale_scorer",
    "VideoTrueFalseScorer": "pyrit.score.true_false.video_true_false_scorer",
    "XSSOutputScorer": "pyrit.score.true_false.regex.xss_output_scorer",
    "XXEOutputScorer": "pyrit.score.true_false.regex.xxe_output_scorer",
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
