# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Remote dataset loaders with automatic discovery.

Import concrete implementations to trigger registration.
"""

from pyrit.datasets.seed_datasets.remote.aegis_ai_content_safety_dataset import (
    _AegisContentSafetyDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.agent_threat_rules_dataset import (
    ATRCategory,
    ATRDetectionField,
    ATRVariationType,
    _AgentThreatRulesDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.aya_redteaming_dataset import (
    _AyaRedteamingDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.babelscape_alert_dataset import (
    _BabelscapeAlertDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.beaver_tails_dataset import (
    _BeaverTailsDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.categorical_harmful_qa_dataset import (
    _CategoricalHarmfulQADataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.cbt_bench_dataset import (
    _CBTBenchDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.ccp_sensitive_prompts_dataset import (
    _CCPSensitivePromptsDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.coconot_dataset import (
    CoCoNotCategory,
    CoCoNotSplit,
    _CoCoNotContrastDataset,
    _CoCoNotRefusalDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.comic_jailbreak_dataset import (
    COMIC_JAILBREAK_TEMPLATES,
    ComicJailbreakTemplateConfig,
    _ComicJailbreakDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.dangerous_qa_dataset import (
    _DangerousQADataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.darkbench_dataset import (
    _DarkBenchDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.equitymedqa_dataset import (
    _EquityMedQADataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.forbidden_questions_dataset import (
    _ForbiddenQuestionsDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.harmbench_dataset import (
    _HarmBenchDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.harmbench_multimodal_dataset import (
    _HarmBenchMultimodalDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.harmful_qa_dataset import (
    _HarmfulQADataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.hixstest_dataset import (
    HiXSTestLanguage,
    _HiXSTestDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.jbb_behaviors_dataset import (
    _JBBBehaviorsDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.librai_do_not_answer_dataset import (
    _LibrAIDoNotAnswerDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.llm_latent_adversarial_training_dataset import (  # noqa: F401
    _LLMLatentAdversarialTrainingDataset,
)
from pyrit.datasets.seed_datasets.remote.medsafetybench_dataset import (
    _MedSafetyBenchDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.mlcommons_ailuminate_dataset import (
    _MLCommonsAILuminateDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.moral_integrity_corpus_dataset import (
    _MICDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.msts_dataset import (
    _MSTSDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.multilingual_vulnerability_dataset import (  # noqa: F401
    _MultilingualVulnerabilityDataset,
)
from pyrit.datasets.seed_datasets.remote.or_bench_dataset import (
    _ORBench80KDataset,
    _ORBenchHardDataset,
    _ORBenchToxicDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.pku_safe_rlhf_dataset import (
    _PKUSafeRLHFDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.promptintel_dataset import (
    PromptIntelCategory,
    PromptIntelSeverity,
    _PromptIntelDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.red_team_social_bias_dataset import (
    _RedTeamSocialBiasDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.datasets.seed_datasets.remote.salad_bench_dataset import (
    _SaladBenchDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.sgxstest_dataset import (
    SGXSTestLabel,
    _SGXSTestDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.simple_safety_tests_dataset import (
    _SimpleSafetyTestsDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.siuo_dataset import (
    SIUOCategory,
    _SIUODataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.sorry_bench_dataset import (
    _SorryBenchDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.sosbench_dataset import (
    _SOSBenchDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.tdc23_redteaming_dataset import (
    _TDC23RedteamingDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.toxic_chat_dataset import (
    _ToxicChatDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.transphobia_awareness_dataset import (  # noqa: F401
    _TransphobiaAwarenessDataset,
)
from pyrit.datasets.seed_datasets.remote.visual_leak_bench_dataset import (
    VisualLeakBenchCategory,
    VisualLeakBenchPIIType,
    _VisualLeakBenchDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.vlguard_dataset import (
    VLGuardCategory,
    VLGuardSubcategory,
    VLGuardSubset,
    _VLGuardDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.vlsu_multimodal_dataset import (
    _VLSUMultimodalDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.xstest_dataset import (
    _XSTestDataset,
)  # noqa: F401

__all__ = [
    "CoCoNotCategory",
    "CoCoNotSplit",
    "HiXSTestLanguage",
    "PromptIntelCategory",
    "PromptIntelSeverity",
    "SGXSTestLabel",
    "SIUOCategory",
    "VLGuardCategory",
    "VLGuardSubcategory",
    "VLGuardSubset",
    "_AegisContentSafetyDataset",
    "ATRCategory",
    "ATRDetectionField",
    "ATRVariationType",
    "_AgentThreatRulesDataset",
    "_AyaRedteamingDataset",
    "_BabelscapeAlertDataset",
    "_BeaverTailsDataset",
    "_CBTBenchDataset",
    "_CCPSensitivePromptsDataset",
    "_CategoricalHarmfulQADataset",
    "_CoCoNotContrastDataset",
    "_CoCoNotRefusalDataset",
    "_ComicJailbreakDataset",
    "COMIC_JAILBREAK_TEMPLATES",
    "ComicJailbreakTemplateConfig",
    "_DangerousQADataset",
    "_DarkBenchDataset",
    "_EquityMedQADataset",
    "_ForbiddenQuestionsDataset",
    "_HarmBenchDataset",
    "_HarmBenchMultimodalDataset",
    "_HarmfulQADataset",
    "_HiXSTestDataset",
    "_JBBBehaviorsDataset",
    "_LibrAIDoNotAnswerDataset",
    "_LLMLatentAdversarialTrainingDataset",
    "_MedSafetyBenchDataset",
    "_MICDataset",
    "_MLCommonsAILuminateDataset",
    "_MSTSDataset",
    "_MultilingualVulnerabilityDataset",
    "_ORBench80KDataset",
    "_ORBenchHardDataset",
    "_ORBenchToxicDataset",
    "_PKUSafeRLHFDataset",
    "_PromptIntelDataset",
    "_RedTeamSocialBiasDataset",
    "_RemoteDatasetLoader",
    "_SGXSTestDataset",
    "_SaladBenchDataset",
    "_SimpleSafetyTestsDataset",
    "_SIUODataset",
    "_SOSBenchDataset",
    "_SorryBenchDataset",
    "_TDC23RedteamingDataset",
    "_ToxicChatDataset",
    "_TransphobiaAwarenessDataset",
    "_VLGuardDataset",
    "_VLSUMultimodalDataset",
    "_VisualLeakBenchDataset",
    "VisualLeakBenchCategory",
    "VisualLeakBenchPIIType",
    "_XSTestDataset",
]
