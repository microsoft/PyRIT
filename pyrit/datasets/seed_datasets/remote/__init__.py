# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Remote dataset loaders with automatic discovery.

Import concrete implementations to trigger registration.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
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
    from pyrit.datasets.seed_datasets.remote.aya_redteaming_dataset import _AyaRedteamingDataset
    from pyrit.datasets.seed_datasets.remote.babelscape_alert_dataset import _BabelscapeAlertDataset
    from pyrit.datasets.seed_datasets.remote.beaver_tails_dataset import _BeaverTailsDataset
    from pyrit.datasets.seed_datasets.remote.categorical_harmful_qa_dataset import _CategoricalHarmfulQADataset
    from pyrit.datasets.seed_datasets.remote.cbt_bench_dataset import _CBTBenchDataset
    from pyrit.datasets.seed_datasets.remote.ccp_sensitive_prompts_dataset import _CCPSensitivePromptsDataset
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
    from pyrit.datasets.seed_datasets.remote.dangerous_qa_dataset import _DangerousQADataset
    from pyrit.datasets.seed_datasets.remote.darkbench_dataset import _DarkBenchDataset
    from pyrit.datasets.seed_datasets.remote.decoding_trust_toxicity_dataset import (
        DecodingTrustToxicitySubset,
        _DecodingTrustToxicityDataset,
    )
    from pyrit.datasets.seed_datasets.remote.equitymedqa_dataset import _EquityMedQADataset
    from pyrit.datasets.seed_datasets.remote.figstep_dataset import (
        FigStepCategory,
        FigStepVariant,
        _FigStepDataset,
        _FigStepProDataset,
    )
    from pyrit.datasets.seed_datasets.remote.forbidden_questions_dataset import _ForbiddenQuestionsDataset
    from pyrit.datasets.seed_datasets.remote.garak_audio_dataset import _GarakAudioAchillesHeelDataset
    from pyrit.datasets.seed_datasets.remote.garak_package_hallucination_dataset import (
        _GarakCratesDataset,
        _GarakDartDataset,
        _GarakNpmDataset,
        _GarakPerlDataset,
        _GarakPypiDataset,
        _GarakRakuDataset,
        _GarakRubyGemsDataset,
    )
    from pyrit.datasets.seed_datasets.remote.garak_system_prompt_dataset import (
        _GarakDrhSystemPromptDataset,
        _GarakTmSystemPromptDataset,
    )
    from pyrit.datasets.seed_datasets.remote.harmbench_dataset import _HarmBenchDataset
    from pyrit.datasets.seed_datasets.remote.harmbench_multimodal_dataset import _HarmBenchMultimodalDataset
    from pyrit.datasets.seed_datasets.remote.harmful_qa_dataset import _HarmfulQADataset
    from pyrit.datasets.seed_datasets.remote.hixstest_dataset import HiXSTestLanguage, _HiXSTestDataset
    from pyrit.datasets.seed_datasets.remote.jailbreakv_28k_dataset import _JailbreakV28KDataset
    from pyrit.datasets.seed_datasets.remote.jailbreakv_redteam_2k_dataset import _JailbreakVRedteam2KDataset
    from pyrit.datasets.seed_datasets.remote.jbb_behaviors_dataset import _JBBBehaviorsDataset
    from pyrit.datasets.seed_datasets.remote.librai_do_not_answer_dataset import _LibrAIDoNotAnswerDataset
    from pyrit.datasets.seed_datasets.remote.llm_latent_adversarial_training_dataset import (
        _LLMLatentAdversarialTrainingDataset,
    )
    from pyrit.datasets.seed_datasets.remote.medsafetybench_dataset import _MedSafetyBenchDataset
    from pyrit.datasets.seed_datasets.remote.mlcommons_ailuminate_dataset import _MLCommonsAILuminateDataset
    from pyrit.datasets.seed_datasets.remote.mm_safetybench_dataset import (
        MMSafetyBenchCategory,
        MMSafetyBenchVariant,
        _MMSafetyBenchDataset,
    )
    from pyrit.datasets.seed_datasets.remote.moral_integrity_corpus_dataset import _MICDataset
    from pyrit.datasets.seed_datasets.remote.mossbench_dataset import MossBenchOversensitivityType, _MossBenchDataset
    from pyrit.datasets.seed_datasets.remote.msts_dataset import _MSTSDataset
    from pyrit.datasets.seed_datasets.remote.multilingual_vulnerability_dataset import _MultilingualVulnerabilityDataset
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
    from pyrit.datasets.seed_datasets.remote.pku_safe_rlhf_dataset import _PKUSafeRLHFDataset
    from pyrit.datasets.seed_datasets.remote.promptintel_dataset import (
        PromptIntelCategory,
        PromptIntelSeverity,
        _PromptIntelDataset,
    )
    from pyrit.datasets.seed_datasets.remote.red_team_social_bias_dataset import _RedTeamSocialBiasDataset
    from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import _RemoteDatasetLoader
    from pyrit.datasets.seed_datasets.remote.salad_bench_dataset import _SaladBenchDataset
    from pyrit.datasets.seed_datasets.remote.sgxstest_dataset import SGXSTestLabel, _SGXSTestDataset
    from pyrit.datasets.seed_datasets.remote.simple_safety_tests_dataset import _SimpleSafetyTestsDataset
    from pyrit.datasets.seed_datasets.remote.siuo_dataset import SIUOCategory, _SIUODataset
    from pyrit.datasets.seed_datasets.remote.sorry_bench_dataset import _SorryBenchDataset
    from pyrit.datasets.seed_datasets.remote.sosbench_dataset import _SOSBenchDataset
    from pyrit.datasets.seed_datasets.remote.strong_reject_dataset import _StrongRejectDataset
    from pyrit.datasets.seed_datasets.remote.tdc23_redteaming_dataset import _TDC23RedteamingDataset
    from pyrit.datasets.seed_datasets.remote.toxic_chat_dataset import _ToxicChatDataset
    from pyrit.datasets.seed_datasets.remote.transphobia_awareness_dataset import _TransphobiaAwarenessDataset
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
    from pyrit.datasets.seed_datasets.remote.vlsu_multimodal_dataset import _VLSUMultimodalDataset
    from pyrit.datasets.seed_datasets.remote.wildguardmix_dataset import (
        WildGuardMixAdversarial,
        WildGuardMixPromptHarmLabel,
        WildGuardMixSplit,
        _WildGuardMixDataset,
    )
    from pyrit.datasets.seed_datasets.remote.xl_safety_bench_dataset import (
        XLSafetyBenchCountry,
        XLSafetyBenchCulturalCategory,
        XLSafetyBenchJailbreakCategory,
        XLSafetyBenchLanguageMode,
        _XLSafetyBenchCulturalDataset,
        _XLSafetyBenchJailbreakDataset,
        _XLSafetyBenchJailbreakObjectivesDataset,
    )
    from pyrit.datasets.seed_datasets.remote.xstest_dataset import _XSTestDataset

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AegisHarmCategory": "pyrit.datasets.seed_datasets.remote.aegis_ai_content_safety_dataset",
    "CoCoNotCategory": "pyrit.datasets.seed_datasets.remote.coconot_dataset",
    "CoCoNotSplit": "pyrit.datasets.seed_datasets.remote.coconot_dataset",
    "DecodingTrustToxicitySubset": "pyrit.datasets.seed_datasets.remote.decoding_trust_toxicity_dataset",
    "FigStepCategory": "pyrit.datasets.seed_datasets.remote.figstep_dataset",
    "FigStepVariant": "pyrit.datasets.seed_datasets.remote.figstep_dataset",
    "HiXSTestLanguage": "pyrit.datasets.seed_datasets.remote.hixstest_dataset",
    "MMSafetyBenchCategory": "pyrit.datasets.seed_datasets.remote.mm_safetybench_dataset",
    "MMSafetyBenchVariant": "pyrit.datasets.seed_datasets.remote.mm_safetybench_dataset",
    "MossBenchOversensitivityType": "pyrit.datasets.seed_datasets.remote.mossbench_dataset",
    "ODINSecurityBoundary": "pyrit.datasets.seed_datasets.remote.odin_dataset",
    "ODINSeverity": "pyrit.datasets.seed_datasets.remote.odin_dataset",
    "ODINTaxonomyCategory": "pyrit.datasets.seed_datasets.remote.odin_dataset",
    "PromptIntelCategory": "pyrit.datasets.seed_datasets.remote.promptintel_dataset",
    "PromptIntelSeverity": "pyrit.datasets.seed_datasets.remote.promptintel_dataset",
    "SGXSTestLabel": "pyrit.datasets.seed_datasets.remote.sgxstest_dataset",
    "SIUOCategory": "pyrit.datasets.seed_datasets.remote.siuo_dataset",
    "VLGuardCategory": "pyrit.datasets.seed_datasets.remote.vlguard_dataset",
    "VLGuardSubcategory": "pyrit.datasets.seed_datasets.remote.vlguard_dataset",
    "VLGuardSubset": "pyrit.datasets.seed_datasets.remote.vlguard_dataset",
    "WildGuardMixAdversarial": "pyrit.datasets.seed_datasets.remote.wildguardmix_dataset",
    "WildGuardMixPromptHarmLabel": "pyrit.datasets.seed_datasets.remote.wildguardmix_dataset",
    "WildGuardMixSplit": "pyrit.datasets.seed_datasets.remote.wildguardmix_dataset",
    "_AegisContentSafetyDataset": "pyrit.datasets.seed_datasets.remote.aegis_ai_content_safety_dataset",
    "ATRCategory": "pyrit.datasets.seed_datasets.remote.agent_threat_rules_dataset",
    "ATRDetectionField": "pyrit.datasets.seed_datasets.remote.agent_threat_rules_dataset",
    "ATRVariationType": "pyrit.datasets.seed_datasets.remote.agent_threat_rules_dataset",
    "_AgentThreatRulesDataset": "pyrit.datasets.seed_datasets.remote.agent_threat_rules_dataset",
    "_AyaRedteamingDataset": "pyrit.datasets.seed_datasets.remote.aya_redteaming_dataset",
    "_BabelscapeAlertDataset": "pyrit.datasets.seed_datasets.remote.babelscape_alert_dataset",
    "_BeaverTailsDataset": "pyrit.datasets.seed_datasets.remote.beaver_tails_dataset",
    "_CBTBenchDataset": "pyrit.datasets.seed_datasets.remote.cbt_bench_dataset",
    "_CCPSensitivePromptsDataset": "pyrit.datasets.seed_datasets.remote.ccp_sensitive_prompts_dataset",
    "_CategoricalHarmfulQADataset": "pyrit.datasets.seed_datasets.remote.categorical_harmful_qa_dataset",
    "_CoCoNotContrastDataset": "pyrit.datasets.seed_datasets.remote.coconot_dataset",
    "_CoCoNotRefusalDataset": "pyrit.datasets.seed_datasets.remote.coconot_dataset",
    "_ComicJailbreakDataset": "pyrit.datasets.seed_datasets.remote.comic_jailbreak_dataset",
    "COMIC_JAILBREAK_TEMPLATES": "pyrit.datasets.seed_datasets.remote.comic_jailbreak_dataset",
    "ComicJailbreakTemplateConfig": "pyrit.datasets.seed_datasets.remote.comic_jailbreak_dataset",
    "_DangerousQADataset": "pyrit.datasets.seed_datasets.remote.dangerous_qa_dataset",
    "_DarkBenchDataset": "pyrit.datasets.seed_datasets.remote.darkbench_dataset",
    "_DecodingTrustToxicityDataset": "pyrit.datasets.seed_datasets.remote.decoding_trust_toxicity_dataset",
    "_EquityMedQADataset": "pyrit.datasets.seed_datasets.remote.equitymedqa_dataset",
    "_FigStepDataset": "pyrit.datasets.seed_datasets.remote.figstep_dataset",
    "_FigStepProDataset": "pyrit.datasets.seed_datasets.remote.figstep_dataset",
    "_ForbiddenQuestionsDataset": "pyrit.datasets.seed_datasets.remote.forbidden_questions_dataset",
    "_GarakAudioAchillesHeelDataset": "pyrit.datasets.seed_datasets.remote.garak_audio_dataset",
    "_GarakCratesDataset": "pyrit.datasets.seed_datasets.remote.garak_package_hallucination_dataset",
    "_GarakDartDataset": "pyrit.datasets.seed_datasets.remote.garak_package_hallucination_dataset",
    "_GarakDrhSystemPromptDataset": "pyrit.datasets.seed_datasets.remote.garak_system_prompt_dataset",
    "_GarakNpmDataset": "pyrit.datasets.seed_datasets.remote.garak_package_hallucination_dataset",
    "_GarakPerlDataset": "pyrit.datasets.seed_datasets.remote.garak_package_hallucination_dataset",
    "_GarakPypiDataset": "pyrit.datasets.seed_datasets.remote.garak_package_hallucination_dataset",
    "_GarakRakuDataset": "pyrit.datasets.seed_datasets.remote.garak_package_hallucination_dataset",
    "_GarakRubyGemsDataset": "pyrit.datasets.seed_datasets.remote.garak_package_hallucination_dataset",
    "_GarakTmSystemPromptDataset": "pyrit.datasets.seed_datasets.remote.garak_system_prompt_dataset",
    "_HarmBenchDataset": "pyrit.datasets.seed_datasets.remote.harmbench_dataset",
    "_HarmBenchMultimodalDataset": "pyrit.datasets.seed_datasets.remote.harmbench_multimodal_dataset",
    "_HarmfulQADataset": "pyrit.datasets.seed_datasets.remote.harmful_qa_dataset",
    "_HiXSTestDataset": "pyrit.datasets.seed_datasets.remote.hixstest_dataset",
    "_JailbreakV28KDataset": "pyrit.datasets.seed_datasets.remote.jailbreakv_28k_dataset",
    "_JailbreakVRedteam2KDataset": "pyrit.datasets.seed_datasets.remote.jailbreakv_redteam_2k_dataset",
    "_JBBBehaviorsDataset": "pyrit.datasets.seed_datasets.remote.jbb_behaviors_dataset",
    "_LibrAIDoNotAnswerDataset": "pyrit.datasets.seed_datasets.remote.librai_do_not_answer_dataset",
    "_LLMLatentAdversarialTrainingDataset": (
        "pyrit.datasets.seed_datasets.remote.llm_latent_adversarial_training_dataset"
    ),
    "_MedSafetyBenchDataset": "pyrit.datasets.seed_datasets.remote.medsafetybench_dataset",
    "_MICDataset": "pyrit.datasets.seed_datasets.remote.moral_integrity_corpus_dataset",
    "_MLCommonsAILuminateDataset": "pyrit.datasets.seed_datasets.remote.mlcommons_ailuminate_dataset",
    "_MMSafetyBenchDataset": "pyrit.datasets.seed_datasets.remote.mm_safetybench_dataset",
    "_MossBenchDataset": "pyrit.datasets.seed_datasets.remote.mossbench_dataset",
    "_MSTSDataset": "pyrit.datasets.seed_datasets.remote.msts_dataset",
    "_MultilingualVulnerabilityDataset": "pyrit.datasets.seed_datasets.remote.multilingual_vulnerability_dataset",
    "_ODINDataset": "pyrit.datasets.seed_datasets.remote.odin_dataset",
    "_ORBench80KDataset": "pyrit.datasets.seed_datasets.remote.or_bench_dataset",
    "_ORBenchHardDataset": "pyrit.datasets.seed_datasets.remote.or_bench_dataset",
    "_ORBenchToxicDataset": "pyrit.datasets.seed_datasets.remote.or_bench_dataset",
    "_PKUSafeRLHFDataset": "pyrit.datasets.seed_datasets.remote.pku_safe_rlhf_dataset",
    "_PromptIntelDataset": "pyrit.datasets.seed_datasets.remote.promptintel_dataset",
    "_RedTeamSocialBiasDataset": "pyrit.datasets.seed_datasets.remote.red_team_social_bias_dataset",
    "_RemoteDatasetLoader": "pyrit.datasets.seed_datasets.remote.remote_dataset_loader",
    "_SGXSTestDataset": "pyrit.datasets.seed_datasets.remote.sgxstest_dataset",
    "_SaladBenchDataset": "pyrit.datasets.seed_datasets.remote.salad_bench_dataset",
    "_SimpleSafetyTestsDataset": "pyrit.datasets.seed_datasets.remote.simple_safety_tests_dataset",
    "_SIUODataset": "pyrit.datasets.seed_datasets.remote.siuo_dataset",
    "_SOSBenchDataset": "pyrit.datasets.seed_datasets.remote.sosbench_dataset",
    "_SorryBenchDataset": "pyrit.datasets.seed_datasets.remote.sorry_bench_dataset",
    "_StrongRejectDataset": "pyrit.datasets.seed_datasets.remote.strong_reject_dataset",
    "_TDC23RedteamingDataset": "pyrit.datasets.seed_datasets.remote.tdc23_redteaming_dataset",
    "_ToxicChatDataset": "pyrit.datasets.seed_datasets.remote.toxic_chat_dataset",
    "_TransphobiaAwarenessDataset": "pyrit.datasets.seed_datasets.remote.transphobia_awareness_dataset",
    "_VLGuardDataset": "pyrit.datasets.seed_datasets.remote.vlguard_dataset",
    "_VLSUMultimodalDataset": "pyrit.datasets.seed_datasets.remote.vlsu_multimodal_dataset",
    "_VisualLeakBenchDataset": "pyrit.datasets.seed_datasets.remote.visual_leak_bench_dataset",
    "VisualLeakBenchCategory": "pyrit.datasets.seed_datasets.remote.visual_leak_bench_dataset",
    "VisualLeakBenchPIIType": "pyrit.datasets.seed_datasets.remote.visual_leak_bench_dataset",
    "_WildGuardMixDataset": "pyrit.datasets.seed_datasets.remote.wildguardmix_dataset",
    "XLSafetyBenchCountry": "pyrit.datasets.seed_datasets.remote.xl_safety_bench_dataset",
    "XLSafetyBenchCulturalCategory": "pyrit.datasets.seed_datasets.remote.xl_safety_bench_dataset",
    "XLSafetyBenchJailbreakCategory": "pyrit.datasets.seed_datasets.remote.xl_safety_bench_dataset",
    "XLSafetyBenchLanguageMode": "pyrit.datasets.seed_datasets.remote.xl_safety_bench_dataset",
    "_XLSafetyBenchCulturalDataset": "pyrit.datasets.seed_datasets.remote.xl_safety_bench_dataset",
    "_XLSafetyBenchJailbreakDataset": "pyrit.datasets.seed_datasets.remote.xl_safety_bench_dataset",
    "_XLSafetyBenchJailbreakObjectivesDataset": "pyrit.datasets.seed_datasets.remote.xl_safety_bench_dataset",
    "_XSTestDataset": "pyrit.datasets.seed_datasets.remote.xstest_dataset",
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
