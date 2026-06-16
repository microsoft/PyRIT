# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Remote dataset loaders with automatic discovery.

Import concrete implementations to trigger registration.
"""

from pyrit.datasets.seed_datasets.remote.aegis_ai_content_safety_dataset import (
    AegisHarmCategory,
    _AegisContentSafetyDataset,
)
from pyrit.datasets.seed_datasets.remote.agent_threat_rules_dataset import (
    ATRCategory,
    ATRDetectionField,
    ATRVariationType,
    _AgentThreatRulesDataset,
)
from pyrit.datasets.seed_datasets.remote.aya_redteaming_dataset import (
    _AyaRedteamingDataset,
)
from pyrit.datasets.seed_datasets.remote.babelscape_alert_dataset import (
    _BabelscapeAlertDataset,
)
from pyrit.datasets.seed_datasets.remote.beaver_tails_dataset import (
    _BeaverTailsDataset,
)
from pyrit.datasets.seed_datasets.remote.categorical_harmful_qa_dataset import (
    _CategoricalHarmfulQADataset,
)
from pyrit.datasets.seed_datasets.remote.cbt_bench_dataset import (
    _CBTBenchDataset,
)
from pyrit.datasets.seed_datasets.remote.ccp_sensitive_prompts_dataset import (
    _CCPSensitivePromptsDataset,
)
from pyrit.datasets.seed_datasets.remote.coconot_dataset import (
    CoCoNotCategory,
    CoCoNotSplit,
    _CoCoNotContrastDataset,
    _CoCoNotRefusalDataset,
)
from pyrit.datasets.seed_datasets.remote.comic_jailbreak_dataset import (
    COMIC_JAILBREAK_TEMPLATES,
    ComicJailbreakTemplateConfig,
    _ComicJailbreakDataset,
)
from pyrit.datasets.seed_datasets.remote.dangerous_qa_dataset import (
    _DangerousQADataset,
)
from pyrit.datasets.seed_datasets.remote.darkbench_dataset import (
    _DarkBenchDataset,
)
from pyrit.datasets.seed_datasets.remote.decoding_trust_toxicity_dataset import (
    DecodingTrustToxicitySubset,
    _DecodingTrustToxicityDataset,
)
from pyrit.datasets.seed_datasets.remote.equitymedqa_dataset import (
    _EquityMedQADataset,
)
from pyrit.datasets.seed_datasets.remote.figstep_dataset import (
    FigStepCategory,
    FigStepVariant,
    _FigStepDataset,
)
from pyrit.datasets.seed_datasets.remote.forbidden_questions_dataset import (
    _ForbiddenQuestionsDataset,
)
from pyrit.datasets.seed_datasets.remote.harmbench_dataset import (
    _HarmBenchDataset,
)
from pyrit.datasets.seed_datasets.remote.harmbench_multimodal_dataset import (
    _HarmBenchMultimodalDataset,
)
from pyrit.datasets.seed_datasets.remote.harmful_qa_dataset import (
    _HarmfulQADataset,
)
from pyrit.datasets.seed_datasets.remote.hixstest_dataset import (
    HiXSTestLanguage,
    _HiXSTestDataset,
)
from pyrit.datasets.seed_datasets.remote.jailbreakv_28k_dataset import (
    _JailbreakV28KDataset,
)
from pyrit.datasets.seed_datasets.remote.jailbreakv_redteam_2k_dataset import (
    _JailbreakVRedteam2KDataset,
)
from pyrit.datasets.seed_datasets.remote.jbb_behaviors_dataset import (
    _JBBBehaviorsDataset,
)
from pyrit.datasets.seed_datasets.remote.librai_do_not_answer_dataset import (
    _LibrAIDoNotAnswerDataset,
)
from pyrit.datasets.seed_datasets.remote.llm_latent_adversarial_training_dataset import (
    _LLMLatentAdversarialTrainingDataset,
)
from pyrit.datasets.seed_datasets.remote.medsafetybench_dataset import (
    _MedSafetyBenchDataset,
)
from pyrit.datasets.seed_datasets.remote.mlcommons_ailuminate_dataset import (
    _MLCommonsAILuminateDataset,
)
from pyrit.datasets.seed_datasets.remote.mm_safetybench_dataset import (
    MMSafetyBenchCategory,
    MMSafetyBenchVariant,
    _MMSafetyBenchDataset,
)
from pyrit.datasets.seed_datasets.remote.moral_integrity_corpus_dataset import (
    _MICDataset,
)
from pyrit.datasets.seed_datasets.remote.mossbench_dataset import (
    MossBenchOversensitivityType,
    _MossBenchDataset,
)
from pyrit.datasets.seed_datasets.remote.msts_dataset import (
    _MSTSDataset,
)
from pyrit.datasets.seed_datasets.remote.multilingual_vulnerability_dataset import (
    _MultilingualVulnerabilityDataset,
)
from pyrit.datasets.seed_datasets.remote.odin_dataset import (
    ODINSecurityBoundary,
    ODINSeverity,
    ODINTaxonomyCategory,
    _ODINDataset,
)
from pyrit.datasets.seed_datasets.remote.or_bench_dataset import (
    _ORBench80KDataset,
    _ORBenchHardDataset,
    _ORBenchToxicDataset,
)
from pyrit.datasets.seed_datasets.remote.pku_safe_rlhf_dataset import (
    _PKUSafeRLHFDataset,
)
from pyrit.datasets.seed_datasets.remote.promptintel_dataset import (
    PromptIntelCategory,
    PromptIntelSeverity,
    _PromptIntelDataset,
)
from pyrit.datasets.seed_datasets.remote.red_team_social_bias_dataset import (
    _RedTeamSocialBiasDataset,
)
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.datasets.seed_datasets.remote.salad_bench_dataset import (
    _SaladBenchDataset,
)
from pyrit.datasets.seed_datasets.remote.sgxstest_dataset import (
    SGXSTestLabel,
    _SGXSTestDataset,
)
from pyrit.datasets.seed_datasets.remote.simple_safety_tests_dataset import (
    _SimpleSafetyTestsDataset,
)
from pyrit.datasets.seed_datasets.remote.siuo_dataset import (
    SIUOCategory,
    _SIUODataset,
)
from pyrit.datasets.seed_datasets.remote.sorry_bench_dataset import (
    _SorryBenchDataset,
)
from pyrit.datasets.seed_datasets.remote.sosbench_dataset import (
    _SOSBenchDataset,
)
from pyrit.datasets.seed_datasets.remote.strong_reject_dataset import (
    _StrongRejectDataset,
)
from pyrit.datasets.seed_datasets.remote.tdc23_redteaming_dataset import (
    _TDC23RedteamingDataset,
)
from pyrit.datasets.seed_datasets.remote.toxic_chat_dataset import (
    _ToxicChatDataset,
)
from pyrit.datasets.seed_datasets.remote.transphobia_awareness_dataset import (
    _TransphobiaAwarenessDataset,
)
from pyrit.datasets.seed_datasets.remote.visual_leak_bench_dataset import (
    VisualLeakBenchCategory,
    VisualLeakBenchPIIType,
    _VisualLeakBenchDataset,
)
from pyrit.datasets.seed_datasets.remote.vlguard_dataset import (
    VLGuardCategory,
    VLGuardSubcategory,
    VLGuardSubset,
    _VLGuardDataset,
)
from pyrit.datasets.seed_datasets.remote.vlsu_multimodal_dataset import (
    _VLSUMultimodalDataset,
)
from pyrit.datasets.seed_datasets.remote.xstest_dataset import (
    _XSTestDataset,
)

__all__ = [
    "AegisHarmCategory",
    "CoCoNotCategory",
    "CoCoNotSplit",
    "DecodingTrustToxicitySubset",
    "FigStepCategory",
    "FigStepVariant",
    "HiXSTestLanguage",
    "MMSafetyBenchCategory",
    "MMSafetyBenchVariant",
    "MossBenchOversensitivityType",
    "ODINSecurityBoundary",
    "ODINSeverity",
    "ODINTaxonomyCategory",
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
    "_DecodingTrustToxicityDataset",
    "_EquityMedQADataset",
    "_FigStepDataset",
    "_ForbiddenQuestionsDataset",
    "_HarmBenchDataset",
    "_HarmBenchMultimodalDataset",
    "_HarmfulQADataset",
    "_HiXSTestDataset",
    "_JailbreakV28KDataset",
    "_JailbreakVRedteam2KDataset",
    "_JBBBehaviorsDataset",
    "_LibrAIDoNotAnswerDataset",
    "_LLMLatentAdversarialTrainingDataset",
    "_MedSafetyBenchDataset",
    "_MICDataset",
    "_MLCommonsAILuminateDataset",
    "_MMSafetyBenchDataset",
    "_MossBenchDataset",
    "_MSTSDataset",
    "_MultilingualVulnerabilityDataset",
    "_ODINDataset",
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
    "_StrongRejectDataset",
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
