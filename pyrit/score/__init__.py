# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scoring functionality for evaluating AI model responses across various dimensions
including harm detection, objective completion, and content classification.
"""

from pyrit.score.batch_scorer import BatchScorer
from pyrit.score.conversation_scorer import ConversationScorer, create_conversation_scorer
from pyrit.score.float_scale.azure_content_filter_scorer import AzureContentFilterScorer
from pyrit.score.float_scale.float_scale_score_aggregator import (
    FloatScaleScoreAggregator,
    FloatScaleScorerAllCategories,
    FloatScaleScorerByCategory,
)
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.float_scale.insecure_code_scorer import InsecureCodeScorer
from pyrit.score.float_scale.plagiarism_scorer import PlagiarismMetric, PlagiarismScorer
from pyrit.score.float_scale.self_ask_general_float_scale_scorer import SelfAskGeneralFloatScaleScorer
from pyrit.score.float_scale.self_ask_likert_scorer import LikertScaleEvalFiles, LikertScalePaths, SelfAskLikertScorer
from pyrit.score.float_scale.self_ask_scale_scorer import SelfAskScaleScorer
from pyrit.score.float_scale.video_float_scale_scorer import VideoFloatScaleScorer
from pyrit.score.human.human_in_the_loop_gradio import HumanInTheLoopScorerGradio
from pyrit.score.printer import ConsoleScorerPrinter, ScorerPrinter
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
    get_all_harm_metrics,
    get_all_objective_metrics,
)
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.decoding_scorer import DecodingScorer
from pyrit.score.true_false.float_scale_threshold_scorer import FloatScaleThresholdScorer
from pyrit.score.true_false.gandalf_scorer import GandalfScorer
from pyrit.score.true_false.markdown_injection import MarkdownInjectionScorer
from pyrit.score.true_false.prompt_shield_scorer import PromptShieldScorer
from pyrit.score.true_false.question_answer_scorer import QuestionAnswerScorer
from pyrit.score.true_false.self_ask_category_scorer import ContentClassifierPaths, SelfAskCategoryScorer
from pyrit.score.true_false.self_ask_general_true_false_scorer import SelfAskGeneralTrueFalseScorer
from pyrit.score.true_false.self_ask_question_answer_scorer import SelfAskQuestionAnswerScorer
from pyrit.score.true_false.self_ask_refusal_scorer import SelfAskRefusalScorer
from pyrit.score.true_false.self_ask_true_false_scorer import (
    SelfAskTrueFalseScorer,
    TrueFalseQuestion,
    TrueFalseQuestionPaths,
)
from pyrit.score.true_false.substring_scorer import SubStringScorer
from pyrit.score.true_false.true_false_composite_scorer import TrueFalseCompositeScorer
from pyrit.score.true_false.true_false_inverter_scorer import TrueFalseInverterScorer
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc, TrueFalseScoreAggregator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer
from pyrit.score.true_false.video_true_false_scorer import VideoTrueFalseScorer

__all__ = [
    "AzureContentFilterScorer",
    "BatchScorer",
    "ContentClassifierPaths",
    "ConsoleScorerPrinter",
    "ConversationScorer",
    "DecodingScorer",
    "create_conversation_scorer",
    "FloatScaleScoreAggregator",
    "FloatScaleScorerAllCategories",
    "FloatScaleScorerByCategory",
    "FloatScaleScorer",
    "FloatScaleThresholdScorer",
    "GandalfScorer",
    "HarmHumanLabeledEntry",
    "HarmScorerEvaluator",
    "HarmScorerMetrics",
    "HumanInTheLoopScorerGradio",
    "HumanLabeledDataset",
    "HumanLabeledEntry",
    "InsecureCodeScorer",
    "LikertScaleEvalFiles",
    "LikertScalePaths",
    "MarkdownInjectionScorer",
    "MetricsType",
    "ObjectiveHumanLabeledEntry",
    "ObjectiveScorerEvaluator",
    "ObjectiveScorerMetrics",
    "PlagiarismMetric",
    "PlagiarismScorer",
    "PromptShieldScorer",
    "QuestionAnswerScorer",
    "RegistryUpdateBehavior",
    "Scorer",
    "ScorerEvalDatasetFiles",
    "ScorerEvaluator",
    "ScorerMetrics",
    "ScorerMetricsWithIdentity",
    "get_all_harm_metrics",
    "get_all_objective_metrics",
    "ScorerPromptValidator",
    "SelfAskCategoryScorer",
    "SelfAskGeneralFloatScaleScorer",
    "SelfAskGeneralTrueFalseScorer",
    "SelfAskLikertScorer",
    "SelfAskQuestionAnswerScorer",
    "SelfAskRefusalScorer",
    "SelfAskScaleScorer",
    "SelfAskTrueFalseScorer",
    "ScorerPrinter",
    "SubStringScorer",
    "TrueFalseCompositeScorer",
    "TrueFalseInverterScorer",
    "TrueFalseQuestion",
    "TrueFalseQuestionPaths",
    "TrueFalseScoreAggregator",
    "TrueFalseAggregatorFunc",
    "TrueFalseScorer",
    "VideoFloatScaleScorer",
    "VideoTrueFalseScorer",
]
