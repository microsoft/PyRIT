# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _MICDataset(_RemoteDatasetLoader):
    """
    Loader for the SALT-NLP Moral Integrity Corpus (MIC) dataset.

    This dataset contains conversations between humans and chatbots
    labeled with moral categories like loyalty, care, fairness,
    authority, sanctity and liberty. After deduplication on the
    question field, the dataset yields tens of thousands of unique
    moral integrity prompts.

    Reference: [@ziems2022mic]
    HuggingFace: https://huggingface.co/datasets/SALT-NLP/MIC

    Warning: Due to the nature of these prompts, consult your legal
    department before testing them with LLMs.
    """

    HF_DATASET_NAME = "SALT-NLP/MIC"
    harm_categories = {"care", "fairness", "loyalty", "authority", "sanctity", "liberty"}
    modalities = ["text"]
    size = "huge"
    tags = {"safety", "ethics", "multiturn"}
    VALID_SPLITS = ["train", "dev", "test"]
    AUTHORS = ["Caleb Ziems", "Jane Yu", "Yi-Chia Wang", "Alon Halevy", "Diyi Yang"]

    def __init__(self) -> None:
        """Initialize the MIC dataset loader."""
        self.source = "https://huggingface.co/datasets/SALT-NLP/MIC/resolve/main/MIC.zip"

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "moral_integrity_corpus"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the MIC dataset and return as SeedDataset.

        Args:
            cache: Whether to cache the downloaded archive on disk. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing MIC prompts.

        Raises:
            ValueError: If the dataset is empty after loading.
        """
        logger.info("Downloading SALT-NLP MIC dataset...")

        inner_files = [f"MIC/{split}.jsonl" for split in self.VALID_SPLITS]
        split_rows = await self._fetch_zip_from_url(
            source=self.source,
            inner_files=inner_files,
            cache=cache,
        )

        seed_prompts: list[SeedPrompt] = []
        seen_questions: set[str] = set()

        for inner in inner_files:
            for row in split_rows[inner]:
                question_raw = row.get("Q")
                if not isinstance(question_raw, str):
                    continue
                question = question_raw.strip()
                if not question or question in seen_questions:
                    continue
                seen_questions.add(question)

                moral = row.get("moral")
                categories = [m.strip() for m in moral.split("|") if m.strip()] if isinstance(moral, str) else []

                seed_prompts.append(
                    SeedPrompt(
                        value=question,
                        data_type="text",
                        dataset_name=self.dataset_name,
                        source=self.source,
                        harm_categories=categories,
                        authors=self.AUTHORS,
                    )
                )

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty.")

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from MIC dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
