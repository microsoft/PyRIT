# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
from typing import Any

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _ToxicChatDataset(_RemoteDatasetLoader):
    """
    Loader for the ToxicChat dataset from HuggingFace.

    ToxicChat contains approximately 10k real user-chatbot conversations from the Chatbot Arena,
    annotated for toxicity and jailbreaking attempts. It provides real-world examples of
    how users interact with LLMs in adversarial ways.

    References:
        - https://huggingface.co/datasets/lmsys/toxic-chat
        - [@lin2023toxicchat]
    License: CC BY-NC 4.0

    Warning: This dataset contains toxic, offensive, and jailbreaking content from real user
    conversations. Consult your legal department before using these prompts for testing.
    """

    HF_DATASET_NAME: str = "lmsys/toxic-chat"

    OPENAI_MODERATION_THRESHOLD: float = 0.8

    def __init__(
        self,
        *,
        config: str = "toxicchat0124",
        split: str = "train",
    ):
        """
        Initialize the ToxicChat dataset loader.

        Args:
            config: Dataset configuration. Defaults to "toxicchat0124".
            split: Dataset split to load. Defaults to "train".
        """
        self.config = config
        self.split = split

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "toxic_chat"

    def _extract_harm_categories(self, item: dict[str, Any]) -> list[str]:
        """
        Extract harm categories from toxicity, jailbreaking, and openai_moderation fields.

        Args:
            item: A single dataset row.

        Returns:
            list[str]: Harm category labels for this entry.
        """
        categories: list[str] = []

        if item.get("toxicity") == 1:
            categories.append("toxicity")
        if item.get("jailbreaking") == 1:
            categories.append("jailbreaking")

        openai_mod = item.get("openai_moderation", "[]")
        try:
            moderation_scores = json.loads(openai_mod) if isinstance(openai_mod, str) else openai_mod
            for category, score in moderation_scores:
                if score > self.OPENAI_MODERATION_THRESHOLD:
                    categories.append(category)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.debug(f"Could not parse openai_moderation for conv_id={item.get('conv_id', 'unknown')}")

        return categories

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch ToxicChat dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the ToxicChat user inputs.
        """
        logger.info(f"Loading ToxicChat dataset from {self.HF_DATASET_NAME}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.HF_DATASET_NAME,
            config=self.config,
            split=self.split,
            cache=cache,
        )

        authors = [
            "Zi Lin",
            "Zihan Wang",
            "Yongqi Tong",
            "Yangkun Wang",
            "Yuxin Guo",
            "Yujia Wang",
            "Jingbo Shang",
        ]
        description = (
            "ToxicChat contains ~10k real user-chatbot conversations from the Chatbot Arena, "
            "annotated for toxicity and jailbreaking attempts. It provides real-world examples "
            "of adversarial user interactions with LLMs."
        )

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = ["UC San Diego"]

        seed_prompts: list[SeedPrompt] = []
        for item in data:
            user_input = item["user_input"]
            harm_categories = self._extract_harm_categories(item)
            prompt = SeedPrompt(
                value=user_input,
                data_type="text",
                dataset_name=self.dataset_name,
                description=description,
                source=source_url,
                authors=authors,
                groups=groups,
                harm_categories=harm_categories,
                metadata={
                    "toxicity": str(item.get("toxicity", "")),
                    "jailbreaking": str(item.get("jailbreaking", "")),
                    "human_annotation": str(item.get("human_annotation", "")),
                },
            )
            seed_prompts.append(prompt)

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from ToxicChat dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
