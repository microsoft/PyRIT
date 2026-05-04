# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

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

    def __init__(
        self,
        *,
        source: str = "TrustAIRLab/forbidden_question_set",
        split: str = "default",
    ) -> None:
        """
        Initialize the Forbidden Questions dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "TrustAIRLab/forbidden_question_set".
            split: Dataset split to load. Defaults to "default".
        """
        self.source = source
        self.split = split

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "forbidden_questions"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch Forbidden Questions dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing forbidden questions with harm categories.
        """
        logger.info(f"Loading Forbidden Questions dataset from {self.source}")

        # Load from HuggingFace
        data = await self._fetch_from_huggingface(
            dataset_name=self.source,
            config=self.split,
            split="train",
            cache=cache,
        )

        authors = ["Xinyue Shen", "Zeyuan Chen", "Michael Backes", "Yun Shen", "Yang Zhang"]
        description = """This is the Forbidden Question Set dataset proposed in the ACM CCS 2024 paper
"Do Anything Now": Characterizing and Evaluating In-The-Wild Jailbreak Prompts on Large Language Models.
It contains 390 questions (= 13 scenarios x 30 questions) adopted from OpenAI Usage Policy.
The focus is on 13 scenarios, including Illegal Activity, Hate Speech, Malware Generation,
Physical Harm, Economic Harm, Fraud, Pornography, Political Lobbying, Privacy Violence, Legal Opinion,
Financial Advice, Health Consultation, and Government Decision."""

        seed_prompts = [
            SeedPrompt(
                value=item["question"],
                data_type="text",
                dataset_name=self.dataset_name,
                authors=authors,
                harm_categories=item["content_policy_name"],
                source="https://huggingface.co/datasets/TrustAIRLab/forbidden_question_set",
                description=description,
            )
            for item in data
        ]

        logger.info(f"Successfully loaded {len(seed_prompts)} questions from Forbidden Questions dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
