# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import re

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)


class _SaladBenchDataset(_RemoteDatasetLoader):
    """
    Loader for the SALAD-Bench dataset from HuggingFace.

    SALAD-Bench is a hierarchical and comprehensive safety benchmark for large language models.
    It organizes harmful questions into 6 domains, 16 tasks, and 65+ categories,
    totaling about 30k questions. It covers QA, multiple choice, attack-enhanced,
    and defense-enhanced variants.

    References:
        - https://huggingface.co/datasets/walledai/SaladBench
        - [@li2024saladbench]
        - https://github.com/OpenSafetyLab/SALAD-BENCH
    License: Apache 2.0

    Warning: This dataset contains harmful and unsafe content designed for safety evaluation.
    """

    HF_DATASET_NAME: str = "walledai/SaladBench"
    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = {
        "academic dishonesty": [HarmCategory.PLAGIARISM],
        "adult content": [HarmCategory.SEXUAL_CONTENT],
        "age stereotyping": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "biological and chemical harms": [HarmCategory.CBRN],
        "campaign materials": [HarmCategory.CAMPAIGNING],
        "child abuse": [HarmCategory.GROOMING, HarmCategory.SEXUAL_CONTENT, HarmCategory.CHILD_LEAKAGE],
        "common sense": [HarmCategory.DANGEROUS_SITUATIONS],
        "copyright infringement": [HarmCategory.COPYRIGHT],
        "cyber attack": [HarmCategory.MALWARE, HarmCategory.COORDINATION_HARM],
        "defamation": [HarmCategory.REPUTATIONAL_DAMAGE],
        "defamation someone": [HarmCategory.REPUTATIONAL_DAMAGE],
        "disability stereotyping": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "disinformation": [HarmCategory.INFO_INTEGRITY],
        "dissemination of dangerous information": [HarmCategory.DANGEROUS_SITUATIONS],
        "drug-related crimes": [
            HarmCategory.DRUG_USE,
            HarmCategory.REGULATED_GOODS,
            HarmCategory.COORDINATION_HARM,
        ],
        "enjoying someone else's pain": [HarmCategory.HARASSMENT],
        "environmental crimes": [HarmCategory.COORDINATION_HARM],
        "erosion of trust in public information": [HarmCategory.INFO_INTEGRITY],
        "erotic chat": [HarmCategory.SEXUAL_CONTENT, HarmCategory.SEXUAL_SOLICITATION],
        "fake news": [HarmCategory.INFO_INTEGRITY],
        "fake online content": [HarmCategory.DECEPTION, HarmCategory.INFO_INTEGRITY],
        "fake review generation": [HarmCategory.DECEPTION, HarmCategory.REPUTATIONAL_DAMAGE],
        "financial crimes": [HarmCategory.SCAMS, HarmCategory.COORDINATION_HARM],
        "financial data leaks": [HarmCategory.PPI, HarmCategory.PROPRIETARY_INFO],
        "financial fraud": [HarmCategory.SCAMS, HarmCategory.DECEPTION],
        "forge identity": [HarmCategory.IMPERSONATION, HarmCategory.FAKE_ACCOUNTS],
        "fraud or deceptive action": [HarmCategory.SCAMS, HarmCategory.DECEPTION],
        "gambling": [HarmCategory.GAMBLING],
        "gender stereotyping": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "government secrets": [HarmCategory.HIGH_RISK_GOVERNMENT, HarmCategory.PROPRIETARY_INFO],
        "group defamation": [
            HarmCategory.HATE_SPEECH,
            HarmCategory.REPUTATIONAL_DAMAGE,
            HarmCategory.REPRESENTATIONAL,
        ],
        "harass, threaten, or bully an individual": [HarmCategory.HARASSMENT, HarmCategory.VIOLENT_THREATS],
        "hate speech": [HarmCategory.HATE_SPEECH],
        "historical revisionism": [HarmCategory.HISTORICAL_EVENTS_BIAS, HarmCategory.INFO_INTEGRITY],
        "human autonomy & integrity": [HarmCategory.COVERT_TARGETED, HarmCategory.DECEPTION],
        "illegal activities": [HarmCategory.COORDINATION_HARM],
        "illegal drugs and regulated/controlled substances": [HarmCategory.DRUG_USE, HarmCategory.REGULATED_GOODS],
        "illegal law advice": [HarmCategory.LEGAL_ADVICE, HarmCategory.COORDINATION_HARM],
        "illegitimate surveillance": [HarmCategory.COVERT_TARGETED, HarmCategory.PPI],
        "inferring personal information": [HarmCategory.PROTECTED_INFERENCE, HarmCategory.PPI],
        "influence operations": [HarmCategory.INFO_INTEGRITY, HarmCategory.COVERT_TARGETED],
        "information & safety": [HarmCategory.DANGEROUS_SITUATIONS],
        "insult": [HarmCategory.HARASSMENT, HarmCategory.PROFANITY],
        "labor issues": [HarmCategory.ALLOCATION],
        "law advice": [HarmCategory.LEGAL_ADVICE],
        "leaking private information": [HarmCategory.PPI],
        "location stereotyping": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "malicious use": [HarmCategory.COORDINATION_HARM],
        "malware generation": [HarmCategory.MALWARE],
        "management or operation of critical infrastructure": [HarmCategory.INFRASTRUCTURE_RISK],
        "manipulation of public opinion": [HarmCategory.INFO_INTEGRITY, HarmCategory.COVERT_TARGETED],
        "medical advice": [HarmCategory.HEALTH_DIAGNOSIS],
        "mimicking writing style": [HarmCategory.IMPERSONATION],
        "misinformation harms": [HarmCategory.INFO_INTEGRITY],
        "multi-level marketing": [HarmCategory.MLM],
        "paypal lending": [HarmCategory.LENDING],
        "persuasion and manipulation": [HarmCategory.COVERT_TARGETED, HarmCategory.DECEPTION],
        "plagiarism": [HarmCategory.PLAGIARISM],
        "pornography": [HarmCategory.SEXUAL_CONTENT],
        "privacy infringement": [HarmCategory.PPI],
        "propagating misconceptions/false beliefs": [HarmCategory.INFO_INTEGRITY],
        "pseudo-pharmaceuticals": [HarmCategory.PSEUDO_PHARMA],
        "psychological manipulation": [HarmCategory.EMOTIONAL, HarmCategory.COVERT_TARGETED],
        "racial and ethnic stereotyping": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "radicalization": [HarmCategory.VIOLENT_EXTREMISM],
        "religious stereotyping": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "representation & toxicity": [HarmCategory.HATE_SPEECH, HarmCategory.REPRESENTATIONAL],
        "risky financial practices": [HarmCategory.FINANCIAL_ADVICE, HarmCategory.FINANCIAL_ELIGIBILITY],
        "scams": [HarmCategory.SCAMS],
        "science denialism": [HarmCategory.INFO_INTEGRITY, HarmCategory.PUBLIC_HEALTH],
        "security threats": [HarmCategory.DANGEROUS_SITUATIONS, HarmCategory.INFRASTRUCTURE_RISK],
        "self-harm": [HarmCategory.SELF_HARM],
        "sexual activity": [HarmCategory.SEXUAL_CONTENT, HarmCategory.SEXUAL_SOLICITATION],
        "sexual offenses": [
            HarmCategory.SEXUAL_CONTENT,
            HarmCategory.SEXUAL_SOLICITATION,
            HarmCategory.NONCONSENSUAL_UPLOAD,
        ],
        "social media rumors": [HarmCategory.INFO_INTEGRITY],
        "socioeconomic harms": [HarmCategory.ALLOCATION],
        "spam": [HarmCategory.SPAM],
        "terrorism": [HarmCategory.VIOLENT_EXTREMISM, HarmCategory.COORDINATION_HARM],
        "theft": [HarmCategory.COORDINATION_HARM],
        "toxic content": [HarmCategory.HARASSMENT, HarmCategory.HATE_SPEECH, HarmCategory.PROFANITY],
        "trade affairs": [HarmCategory.ALLOCATION],
        "trade and compliance": [HarmCategory.COORDINATION_HARM],
        "traffic and driving offenses": [HarmCategory.COORDINATION_HARM],
        "unfair representation": [HarmCategory.REPRESENTATIONAL, HarmCategory.ALLOCATION],
        "violation of personal property": [HarmCategory.COORDINATION_HARM],
        "violent content": [HarmCategory.VIOLENT_CONTENT],
        "violent crimes": [
            HarmCategory.VIOLENT_CONTENT,
            HarmCategory.VIOLENT_THREATS,
            HarmCategory.COORDINATION_HARM,
        ],
        "weapon generation and transportation": [HarmCategory.REGULATED_GOODS],
    }

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "huge"  # 21318 harmful questions across 6 domains, 16 tasks, 65+ categories
    tags: frozenset[str] = frozenset({"default", "safety", "jailbreak"})

    def __init__(
        self,
        *,
        config: str = "prompts",
        split: str = "base",
    ) -> None:
        """
        Initialize the SALAD-Bench dataset loader.

        Args:
            config: Dataset configuration. Defaults to "prompts".
            split: Dataset split to load. One of "base", "attackEnhanced", "defenseEnhanced".
                Defaults to "base".
        """
        self.config = config
        self.split = split

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "salad_bench"

    @staticmethod
    def _parse_category(category: str) -> str:
        """
        Strip leading identifier like 'O6: ' from a category string.

        Args:
            category (str): The category string to parse.

        Returns:
            str: The category string without the leading identifier.
        """
        return re.sub(r"^O\d+:\s*", "", category)

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch SALAD-Bench dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the SALAD-Bench prompts.
        """
        logger.info(f"Loading SALAD-Bench dataset from {self.HF_DATASET_NAME}")

        data = await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            config=self.config,
            split=self.split,
            cache=cache,
        )

        authors = [
            "Lijun Li",
            "Bowen Dong",
            "Ruohui Wang",
            "Xuhao Hu",
            "Wangmeng Zuo",
            "Dahua Lin",
            "Yu Qiao",
            "Jing Shao",
        ]
        description = (
            "SALAD-Bench is a hierarchical and comprehensive safety benchmark for large language "
            "models (ACL 2024). It contains about 30k questions organized into 6 domains, 16 tasks, "
            "and 65+ categories, with base, attack-enhanced, and defense-enhanced variants."
        )

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = [
            "Shanghai Artificial Intelligence Laboratory",
            "Harbin Institute of Technology",
            "Beijing Institute of Technology",
            "The Chinese University of Hong Kong",
            "The Hong Kong Polytechnic University",
        ]

        seed_prompts: list[SeedUnion] = []
        for item in data:
            parsed_categories = [self._parse_category(c) for c in item["categories"]]
            metadata: dict[str, str | int] = {"categories": json.dumps(item["categories"])}
            if source := item.get("source"):
                metadata["original_source"] = source

            seed_prompts.append(
                SeedPrompt(
                    value=item["prompt"],
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=self._standardize_harm_categories(
                        parsed_categories,
                        alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES,
                    ),
                    description=description,
                    source=source_url,
                    authors=authors,
                    groups=groups,
                    metadata=metadata,
                )
            )

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from SALAD-Bench dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
