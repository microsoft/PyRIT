# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Remote dataset loaders with automatic discovery.

Import concrete implementations to trigger registration.
"""

from pyrit.datasets.seed_datasets.remote.aegis_ai_content_safety_dataset import (
    _AegisContentSafetyDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.aya_redteaming_dataset import (
    _AyaRedteamingDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.babelscape_alert_dataset import (
    _BabelscapeAlertDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.ccp_sensitive_prompts_dataset import (
    _CCPSensitivePromptsDataset,
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
from pyrit.datasets.seed_datasets.remote.multilingual_vulnerability_dataset import (  # noqa: F401
    _MultilingualVulnerabilityDataset,
)
from pyrit.datasets.seed_datasets.remote.pku_safe_rlhf_dataset import (
    _PKUSafeRLHFDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.red_team_social_bias_dataset import (
    _RedTeamSocialBiasDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.datasets.seed_datasets.remote.sorry_bench_dataset import (
    _SorryBenchDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.sosbench_dataset import (
    _SOSBenchDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.tdc23_redteaming_dataset import (
    _TDC23RedteamingDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.transphobia_awareness_dataset import (  # noqa: F401
    _TransphobiaAwarenessDataset,
)
from pyrit.datasets.seed_datasets.remote.vlsu_multimodal_dataset import (
    _VLSUMultimodalDataset,
)  # noqa: F401
from pyrit.datasets.seed_datasets.remote.xstest_dataset import (
    _XSTestDataset,
)  # noqa: F401

__all__ = [
    "_RemoteDatasetLoader",
    "_AegisContentSafetyDataset",
    "_AyaRedteamingDataset",
    "_BabelscapeAlertDataset",
    "_CCPSensitivePromptsDataset",
    "_DarkBenchDataset",
    "_EquityMedQADataset",
    "_ForbiddenQuestionsDataset",
    "_HarmBenchDataset",
    "_HarmBenchMultimodalDataset",
    "_JBBBehaviorsDataset",
    "_LibrAIDoNotAnswerDataset",
    "_MedSafetyBenchDataset",
    "_MLCommonsAILuminateDataset",
    "_PKUSafeRLHFDataset",
    "_RedTeamSocialBiasDataset",
    "_SorryBenchDataset",
    "_SOSBenchDataset",
    "_TDC23RedteamingDataset",
    "_VLSUMultimodalDataset",
    "_XSTestDataset",
]
