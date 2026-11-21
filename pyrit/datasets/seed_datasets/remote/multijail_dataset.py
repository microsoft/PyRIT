# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
from enum import Enum

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import _RemoteDatasetLoader
from pyrit.models import SeedDataset, SeedPrompt


class MultiJailLanguage(Enum):
    """Languages available in the MultiJail dataset."""

    ENGLISH = "en"
    CHINESE = "zh"
    ITALIAN = "it"
    VIETNAMESE = "vi"
    ARABIC = "ar"
    KOREAN = "ko"
    THAI = "th"
    BENGALI = "bn"
    SWAHILI = "sw"
    JAVANESE = "jv"


class _MultiJailDataset(_RemoteDatasetLoader):
    """
    Loader for the human-translated unsafe prompts in the MultiJail dataset.

    Reference: [@deng2024multilingual]
    HuggingFace: https://huggingface.co/datasets/DAMO-NLP-SG/MultiJail
    License: MIT

    Warning: Due to the nature of these prompts, consult your legal department
    before testing them with LLMs to ensure compliance and reduce potential risks.
    """

    _AUTHORS = ["Yue Deng", "Wenxuan Zhang", "Sinno Jialin Pan", "Lidong Bing"]
    _GROUPS = [
        "DAMO Academy, Alibaba Group",
        "Nanyang Technological University",
        "Hupan Lab",
        "The Chinese University of Hong Kong",
    ]
    HF_DATASET_NAME: str = "DAMO-NLP-SG/MultiJail"

    modalities: list[str] = ["text"]
    size: str = "large"  # 315 prompts across 10 languages
    tags: set[str] = {"default", "safety", "multilingual"}
    harm_categories: list[str] = [
        "adult content",
        "animal abuse",
        "bullying & harassment",
        "child abuse",
        "conspiracy theories & misinformation",
        "discrimination & injustice",
        "fraud & deception",
        "hate speech & offensive language",
        "non-violent unethical behavior (e.g. lying, cheating, etc.)",
        "property crime & vandalism",
        "self-harm",
        "sexual exploitation & human trafficking",
        "soliciting personally identifiable information",
        "substance abuse & banned substances",
        "terrorism & organized crime",
        "theft",
        "violence & incitement",
        "weapons",
    ]

    def __init__(self, *, languages: list[MultiJailLanguage] | None = None) -> None:
        """Initialize the MultiJail dataset loader."""
        if languages is None:
            self.languages = list(MultiJailLanguage)
        else:
            self._validate_enums(languages, MultiJailLanguage, "language")
            self.languages = languages

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "multijail"

    @override
    async def _fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch MultiJail prompts for the selected languages.

        Returns:
            SeedDataset: The prompts for the selected languages.

        Raises:
            ValueError: If no prompts are produced.
        """
        rows = await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            config="default",
            split="train",
            cache=cache,
        )
        seeds: list[SeedPrompt] = []

        for row in rows:
            raw_tags = row.get("tags")
            if isinstance(raw_tags, str):
                try:
                    parsed_tags = ast.literal_eval(raw_tags)
                except (SyntaxError, ValueError):
                    parsed_tags = []
            else:
                parsed_tags = raw_tags if isinstance(raw_tags, list) else []
            harm_categories = [str(category).strip() for category in parsed_tags if str(category).strip()]

            for language in self.languages:
                text = row.get(language.value)
                if not isinstance(text, str) or not text.strip():
                    continue
                seeds.append(
                    SeedPrompt(
                        value=text,
                        data_type="text",
                        dataset_name=self.dataset_name,
                        source=f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}",
                        authors=self._AUTHORS,
                        groups=self._GROUPS,
                        harm_categories=harm_categories,
                        metadata={
                            "multijail_language": language.value,
                            "multijail_id": str(row.get("id", "")),
                            "multijail_source": str(row.get("source", "")),
                        },
                    )
                )

        if not seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)
