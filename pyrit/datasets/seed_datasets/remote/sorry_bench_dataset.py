# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from typing import Optional, cast

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)


class _SorryBenchDataset(_RemoteDatasetLoader):
    """
    Loader for the Sorry-Bench dataset (updated 2025/03 version).

    This dataset contains adversarial prompts designed to test LLM safety
    across 44 categories with 21 different prompt styles (base + 20 linguistic mutations).

    Reference: [@xie2024sorrybench]
    """

    VALID_CATEGORIES = [
        "Personal Insulting Words",
        "Social-group Insulting Words",
        "Threatening Words",
        "Lewd and Obscene Words",
        "Libelous Words",
        "Self-Harm",
        "Violent Crimes",
        "Harassment",
        "Sexual Crimes",
        "Property Crimes",
        "Public Order Crimes",
        "System Intrusion",
        "Malware Code Generation",
        "Fraud",
        "Financial Crimes",
        "IP Infringement",
        "PII Violations",
        "Illegal Crafting",
        "Terrorism",
        "Child-related Crimes",
        "Animal-related Crimes",
        "Environmental Crimes",
        "Evasion of Law Enforcement",
        "Human Elimination",
        "Advice on Adult Content",
        "Sexual Explicit Content Generation",
        "Non-sexual Explicit Content Generation",
        "Fake News Generation",
        "False Advertising",
        "Advice on Discrimination Behaviors",
        "Military Use",
        "Political Belief Promotion",
        "Ethical Belief Promotion",
        "Religion Promotion",
        "Social Stereotype Promotion",
        "Extremist Content Promotion",
        "Conspiracy Theories Promotion",
        "False Common Knowledge Promotion",
        "Harmful & Unhealthy Behaviors Promotion",
        "Medical Advice",
        "Financial Advice",
        "Legal Consulting Advice",
        "Governance Decision Advice",
        "Dangerous Machinery Operation Advice",
    ]

    VALID_PROMPT_STYLES = [
        "base",
        "ascii",
        "atbash",
        "authority_endorsement",
        "caesar",
        "evidence-based_persuasion",
        "expert_endorsement",
        "logical_appeal",
        "misrepresentation",
        "misspellings",
        "morse",
        "question",
        "role_play",
        "slang",
        "technical_terms",
        "translate-fr",
        "translate-ml",
        "translate-mr",
        "translate-ta",
        "translate-zh-cn",
        "uncommon_dialects",
    ]

    def __init__(
        self,
        *,
        source: str = "sorry-bench/sorry-bench-202503",
        categories: Optional[list[str]] = None,
        prompt_style: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        """
        Initialize the Sorry-Bench dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "sorry-bench/sorry-bench-202503".
            categories: Optional list of categories to filter. Defaults to None (all categories).
            prompt_style: Optional prompt style to filter. Defaults to "base".
                Available: "base", "ascii", "caesar", "slang", "authority_endorsement", etc.
            token: Hugging Face authentication token. If not provided, reads from HUGGINGFACE_TOKEN env var.

        Raises:
            ValueError: If invalid categories or prompt_style are provided.
        """
        self.source = source
        self.categories = categories
        self.prompt_style = prompt_style if prompt_style is not None else "base"
        self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")

        # Validate prompt_style
        if self.prompt_style not in self.VALID_PROMPT_STYLES:
            raise ValueError(
                f"Invalid prompt_style '{self.prompt_style}'. Must be one of: {', '.join(self.VALID_PROMPT_STYLES)}"
            )

        # Validate categories
        if categories:
            invalid_categories = [cat for cat in categories if cat not in self.VALID_CATEGORIES]
            if invalid_categories:
                raise ValueError(
                    f"Invalid categories: {invalid_categories}. Must be from the list of 44 valid categories. "
                    f"See: https://huggingface.co/datasets/sorry-bench/sorry-bench-202503/blob/main/meta_info.py"
                )

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "sorry_bench"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch Sorry-Bench dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing Sorry-Bench prompts with harm categories.

        Raises:
            ValueError: If the dataset is empty after filtering.
        """
        try:
            logger.info(f"Loading Sorry-Bench dataset from {self.source}")

            data = await self._fetch_from_huggingface(
                dataset_name=self.source,
                split="train",
                cache=cache,
                token=self.token,
            )

            common_metadata = {
                "dataset_name": self.dataset_name,
                "authors": ["Sorry-Bench Team"],
                "description": "Adversarial prompts for testing LLM safety across 44 categories",
                "source": self.source,
                "data_type": "text",
                "name": "Sorry-Bench 2025-03",
            }
            # sorry-bench-202503 stores `category` as a 1-indexed number ("1".."44"),
            # not the human-readable name. Map it back to the name so filtering,
            # metadata and harm-category standardization all operate on the label.
            category_num_to_name = {str(i): name for i, name in enumerate(self.VALID_CATEGORIES, start=1)}
            harm_category_alias_overrides: dict[str, list[HarmCategory]] = cast(
                "dict[str, list[HarmCategory]]",
                {
                    "personal insulting words": [HarmCategory.HARASSMENT],
                    "social-group insulting words": [HarmCategory.HATESPEECH, HarmCategory.REPRESENTATIONAL],
                    "threatening words": [HarmCategory.VIOLENT_THREATS],
                    "lewd and obscene words": [HarmCategory.SEXUAL_CONTENT, HarmCategory.PROFANITY],
                    "libelous words": [HarmCategory.REPUTATIONAL_DAMAGE],
                    "self-harm": [HarmCategory.SELF_HARM],
                    "violent crimes": [HarmCategory.COORDINATION_HARM, HarmCategory.VIOLENT_THREATS],
                    "harassment": [HarmCategory.HARASSMENT],
                    "sexual crimes": [HarmCategory.COORDINATION_HARM, HarmCategory.SEXUAL_CONTENT],
                    "property crimes": [HarmCategory.COORDINATION_HARM],
                    "public order crimes": [HarmCategory.COORDINATION_HARM],
                    "system intrusion": [HarmCategory.MALWARE],
                    "malware code generation": [HarmCategory.MALWARE],
                    "fraud": [HarmCategory.SCAMS, HarmCategory.DECEPTION],
                    "financial crimes": [HarmCategory.SCAMS, HarmCategory.DECEPTION],
                    "ip infringement": [HarmCategory.COPYRIGHT, HarmCategory.TRADEMARK],
                    "pii violations": [HarmCategory.PPI],
                    "illegal crafting": [HarmCategory.COORDINATION_HARM, HarmCategory.REGULATED_GOODS],
                    "terrorism": [HarmCategory.VIOLENT_EXTREMISM],
                    "child-related crimes": [HarmCategory.GROOMING, HarmCategory.SEXUALIZATION],
                    "animal-related crimes": [HarmCategory.COORDINATION_HARM],
                    "environmental crimes": [HarmCategory.COORDINATION_HARM],
                    "evasion of law enforcement": [HarmCategory.COORDINATION_HARM],
                    "human elimination": [HarmCategory.VIOLENT_THREATS, HarmCategory.COORDINATION_HARM],
                    "advice on adult content": [HarmCategory.SEXUAL_CONTENT],
                    "sexual explicit content generation": [HarmCategory.SEXUAL_CONTENT],
                    "non-sexual explicit content generation": [HarmCategory.VIOLENT_CONTENT],
                    "fake news generation": [HarmCategory.INFO_INTEGRITY],
                    "false advertising": [HarmCategory.DECEPTION, HarmCategory.SCAMS],
                    "advice on discrimination behaviors": [HarmCategory.REPRESENTATIONAL, HarmCategory.HATESPEECH],
                    "military use": [HarmCategory.MILITARY],
                    "political belief promotion": [HarmCategory.CAMPAIGNING],
                    "ethical belief promotion": [HarmCategory.OTHER],
                    "religion promotion": [HarmCategory.OTHER],
                    "social stereotype promotion": [HarmCategory.REPRESENTATIONAL, HarmCategory.HATESPEECH],
                    "extremist content promotion": [HarmCategory.VIOLENT_EXTREMISM],
                    "conspiracy theories promotion": [HarmCategory.INFO_INTEGRITY],
                    "false common knowledge promotion": [HarmCategory.INFO_INTEGRITY],
                    "harmful & unhealthy behaviors promotion": [
                        HarmCategory.DANGEROUS_SITUATIONS,
                        HarmCategory.PUBLIC_HEALTH,
                    ],
                    "medical advice": [HarmCategory.HEALTH_DIAGNOSIS, HarmCategory.PUBLIC_HEALTH],
                    "financial advice": [HarmCategory.FINANCIAL_ADVICE],
                    "legal consulting advice": [HarmCategory.LEGAL_ADVICE],
                    "governance decision advice": [HarmCategory.HIGH_RISK_GOVERNMENT],
                    "dangerous machinery operation advice": [HarmCategory.DANGEROUS_SITUATIONS],
                },
            )

            seed_prompts = []

            for item in data:
                raw_category = str(item.get("category", "")).strip()
                category = category_num_to_name.get(raw_category, raw_category)
                item_prompt_style = item.get("prompt_style", "")
                turns = [t for t in item.get("turns", []) if t is not None]
                question_id = item.get("question_id")

                if not turns:
                    logger.debug(f"[SorryBench] Skipping item {question_id} - empty turns field")
                    continue

                prompt_text = turns[0].strip()

                if not prompt_text:
                    logger.debug(f"[SorryBench] Skipping item {question_id} - empty prompt text")
                    continue

                if self.categories and category not in self.categories:
                    continue

                if self.prompt_style != item_prompt_style:
                    continue

                standardized_harm_categories = self._standardize_harm_categories(
                    category,
                    alias_overrides=harm_category_alias_overrides,
                )
                seed_prompt = SeedPrompt(
                    value=prompt_text,
                    harm_categories=standardized_harm_categories,
                    groups=[item_prompt_style] if item_prompt_style else [],
                    metadata={
                        "sorry_bench_category": category,
                        "sorry_bench_category_id": raw_category,
                        "prompt_style": item_prompt_style,
                        "question_id": question_id,
                    },
                    **common_metadata,  # type: ignore[ty:invalid-argument-type]
                )

                seed_prompts.append(seed_prompt)

            if not seed_prompts:
                raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

            logger.info(f"Successfully loaded {len(seed_prompts)} prompts from Sorry-Bench dataset")

            return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to load Sorry-Bench dataset: {str(e)}")
            raise
