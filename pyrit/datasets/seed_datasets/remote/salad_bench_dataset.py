# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import re

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

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
    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[str]] = {
        "academic dishonesty": ["PLAGIARISM"],
        "adult content": ["SEXUAL_CONTENT"],
        "age stereotyping": ["HATESPEECH", "REPRESENTATIONAL"],
        "biological and chemical harms": ["CBRN"],
        "campaign materials": ["CAMPAIGNING"],
        "child abuse": ["GROOMING", "SEXUAL_CONTENT", "CHILD_LEAKAGE"],
        "common sense": ["DANGEROUS_SITUATIONS"],
        "copyright infringement": ["COPYRIGHT"],
        "cyber attack": ["MALWARE", "COORDINATION_HARM"],
        "defamation": ["REPUTATIONAL_DAMAGE"],
        "defamation someone": ["REPUTATIONAL_DAMAGE"],
        "disability stereotyping": ["HATESPEECH", "REPRESENTATIONAL"],
        "disinformation": ["INFO_INTEGRITY"],
        "dissemination of dangerous information": ["DANGEROUS_SITUATIONS"],
        "drug-related crimes": ["DRUG_USE", "REGULATED_GOODS", "COORDINATION_HARM"],
        "enjoying someone else's pain": ["HARASSMENT"],
        "environmental crimes": ["COORDINATION_HARM"],
        "erosion of trust in public information": ["INFO_INTEGRITY"],
        "erotic chat": ["SEXUAL_CONTENT", "SEXUAL_SOLICITATION"],
        "fake news": ["INFO_INTEGRITY"],
        "fake online content": ["DECEPTION", "INFO_INTEGRITY"],
        "fake review generation": ["DECEPTION", "REPUTATIONAL_DAMAGE"],
        "financial crimes": ["SCAMS", "COORDINATION_HARM"],
        "financial data leaks": ["PPI", "PROPRIETARY_INFO"],
        "financial fraud": ["SCAMS", "DECEPTION"],
        "forge identity": ["IMPERSONATION", "FAKE_ACCOUNTS"],
        "fraud or deceptive action": ["SCAMS", "DECEPTION"],
        "gambling": ["GAMBLING"],
        "gender stereotyping": ["HATESPEECH", "REPRESENTATIONAL"],
        "government secrets": ["HIGH_RISK_GOVERNMENT", "PROPRIETARY_INFO"],
        "group defamation": ["HATESPEECH", "REPUTATIONAL_DAMAGE", "REPRESENTATIONAL"],
        "harass, threaten, or bully an individual": ["HARASSMENT", "VIOLENT_THREATS"],
        "hate speech": ["HATESPEECH"],
        "historical revisionism": ["HISTORICAL_EVENTS_BIAS", "INFO_INTEGRITY"],
        "human autonomy & integrity": ["COVERT_TARGETED", "DECEPTION"],
        "illegal activities": ["COORDINATION_HARM"],
        "illegal drugs and regulated/controlled substances": ["DRUG_USE", "REGULATED_GOODS"],
        "illegal law advice": ["LEGAL_ADVICE", "COORDINATION_HARM"],
        "illegitimate surveillance": ["COVERT_TARGETED", "PPI"],
        "inferring personal information": ["PROTECTED_INFERENCE", "PPI"],
        "influence operations": ["INFO_INTEGRITY", "COVERT_TARGETED"],
        "information & safety": ["DANGEROUS_SITUATIONS"],
        "insult": ["HARASSMENT", "PROFANITY"],
        "labor issues": ["ALLOCATION"],
        "law advice": ["LEGAL_ADVICE"],
        "leaking private information": ["PPI"],
        "location stereotyping": ["HATESPEECH", "REPRESENTATIONAL"],
        "malicious use": ["COORDINATION_HARM"],
        "malware generation": ["MALWARE"],
        "management or operation of critical infrastructure": ["INFRASTRUCTURE_RISK"],
        "manipulation of public opinion": ["INFO_INTEGRITY", "COVERT_TARGETED"],
        "medical advice": ["HEALTH_DIAGNOSIS"],
        "mimicking writing style": ["IMPERSONATION"],
        "misinformation harms": ["INFO_INTEGRITY"],
        "multi-level marketing": ["MLM"],
        "paypal lending": ["LENDING"],
        "persuasion and manipulation": ["COVERT_TARGETED", "DECEPTION"],
        "plagiarism": ["PLAGIARISM"],
        "pornography": ["SEXUAL_CONTENT"],
        "privacy infringement": ["PPI"],
        "propagating misconceptions/false beliefs": ["INFO_INTEGRITY"],
        "pseudo-pharmaceuticals": ["PSEUDO_PHARMA"],
        "psychological manipulation": ["EMOTIONAL", "COVERT_TARGETED"],
        "racial and ethnic stereotyping": ["HATESPEECH", "REPRESENTATIONAL"],
        "radicalization": ["VIOLENT_EXTREMISM"],
        "religious stereotyping": ["HATESPEECH", "REPRESENTATIONAL"],
        "representation & toxicity": ["HATESPEECH", "REPRESENTATIONAL"],
        "risky financial practices": ["FINANCIAL_ADVICE", "FINANCIAL_ELIGIBILITY"],
        "scams": ["SCAMS"],
        "science denialism": ["INFO_INTEGRITY", "PUBLIC_HEALTH"],
        "security threats": ["DANGEROUS_SITUATIONS", "INFRASTRUCTURE_RISK"],
        "self-harm": ["SELF_HARM"],
        "sexual activity": ["SEXUAL_CONTENT", "SEXUAL_SOLICITATION"],
        "sexual offenses": ["SEXUAL_CONTENT", "SEXUAL_SOLICITATION", "NONCONSENSUAL_UPLOAD"],
        "social media rumors": ["INFO_INTEGRITY"],
        "socioeconomic harms": ["ALLOCATION"],
        "spam": ["SPAM"],
        "terrorism": ["VIOLENT_EXTREMISM", "COORDINATION_HARM"],
        "theft": ["COORDINATION_HARM"],
        "toxic content": ["HARASSMENT", "HATESPEECH", "PROFANITY"],
        "trade affairs": ["ALLOCATION"],
        "trade and compliance": ["COORDINATION_HARM"],
        "traffic and driving offenses": ["COORDINATION_HARM"],
        "unfair representation": ["REPRESENTATIONAL", "ALLOCATION"],
        "violation of personal property": ["COORDINATION_HARM"],
        "violent content": ["VIOLENT_CONTENT"],
        "violent crimes": ["VIOLENT_CONTENT", "VIOLENT_THREATS", "COORDINATION_HARM"],
        "weapon generation and transportation": ["REGULATED_GOODS"],
    }

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
        """Return the dataset name."""
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

        data = await self._fetch_from_huggingface(
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
            "Chinese University of Hong Kong",
            "The Hong Kong Polytechnic University",
        ]

        seed_prompts = []
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
                    harm_categories=list(
                        dict.fromkeys(
                            self._standardize_harm_categories(
                                parsed_categories,
                                alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES,
                            )
                        )
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
