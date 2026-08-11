# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)


class _ForbiddenQuestionsDataset(_RemoteDatasetLoader):
    """
    Loader for the Forbidden Questions dataset.

    This dataset contains 390 questions across 13 scenarios adopted from OpenAI Usage Policy.
    The focus is on scenarios including Illegal Activity, Hate Speech, Malware Generation,
    Physical Harm, Economic Harm, Fraud, Pornography, Political Lobbying, Privacy Violence,
    Legal Opinion, Financial Advice, Health Consultation, and Government Decision.

    Reference: [@shen2023donotanything]
    GitHub: https://github.com/verazuo/jailbreak_llms/
    Website: https://jailbreak-llms.xinyueshen.me/
    """

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "medium"  # 390 questions (13 scenarios x 30 questions)
    tags: frozenset[str] = frozenset({"default", "safety", "jailbreak"})

    def __init__(
        self,
        *,
        source: str = "TrustAIRLab/forbidden_question_set",
    ) -> None:
        """
        Initialize the Forbidden Questions dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "TrustAIRLab/forbidden_question_set".
        """
        self.source = source

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "forbidden_questions"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch Forbidden Questions dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing forbidden questions with harm categories.
        """
        logger.info(f"Loading Forbidden Questions dataset from {self.source}")

        # Load from HuggingFace
        data = await self._fetch_from_huggingface_async(
            dataset_name=self.source,
            config="default",
            split="train",
            cache=cache,
        )

        authors = ["Xinyue Shen", "Zeyuan Chen", "Michael Backes", "Yun Shen", "Yang Zhang"]
        groups = ["CISPA Helmholtz Center for Information Security", "NetApp"]
        description = """This is the Forbidden Question Set dataset proposed in the ACM CCS 2024 paper
"Do Anything Now": Characterizing and Evaluating In-The-Wild Jailbreak Prompts on Large Language Models.
It contains 390 questions (= 13 scenarios x 30 questions) adopted from OpenAI Usage Policy.
The focus is on 13 scenarios, including Illegal Activity, Hate Speech, Malware Generation,
Physical Harm, Economic Harm, Fraud, Pornography, Political Lobbying, Privacy Violence, Legal Opinion,
Financial Advice, Health Consultation, and Government Decision."""

        # Map the 13 policy scenarios to PyRIT harm categories. Keys must match the
        # dataset's actual ``content_policy_name`` values, which are abbreviated
        # relative to the paper's scenario names (e.g. "Malware", "Gov Decision").
        alias_overrides: dict[str, list[HarmCategory]] = {
            "Illegal Activity": [HarmCategory.COORDINATION_HARM],
            "Hate Speech": [HarmCategory.HATE_SPEECH],
            "Malware": [HarmCategory.MALWARE],
            "Physical Harm": [HarmCategory.VIOLENT_CONTENT, HarmCategory.COORDINATION_HARM],
            "Economic Harm": [HarmCategory.SCAMS, HarmCategory.DECEPTION],
            "Fraud": [HarmCategory.SCAMS, HarmCategory.DECEPTION],
            "Pornography": [HarmCategory.SEXUAL_CONTENT],
            "Political Lobbying": [HarmCategory.CAMPAIGNING],
            "Privacy Violence": [HarmCategory.PPI],
            "Legal Opinion": [HarmCategory.LEGAL_ADVICE],
            "Financial Advice": [HarmCategory.FINANCIAL_ADVICE],
            "Health Consultation": [HarmCategory.HEALTH_DIAGNOSIS],
            "Gov Decision": [HarmCategory.HIGH_RISK_GOVERNMENT, HarmCategory.COORDINATION_HARM],
        }

        seed_prompts: list[SeedUnion] = [
            SeedPrompt(
                value=item["question"],
                data_type="text",
                dataset_name=self.dataset_name,
                authors=authors,
                groups=groups,
                harm_categories=self._standardize_harm_categories(
                    item.get("content_policy_name"),
                    alias_overrides=alias_overrides,
                ),
                metadata={"forbidden_questions_content_policy_name": item.get("content_policy_name")},
                source="https://huggingface.co/datasets/TrustAIRLab/forbidden_question_set",
                description=description,
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} questions from Forbidden Questions dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
