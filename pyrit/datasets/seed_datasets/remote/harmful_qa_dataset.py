# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
from functools import lru_cache
from pathlib import Path

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)

# Row-level harm-category map bundled alongside this loader. HarmfulQA's ``topic``
# and ``subtopic`` fields are academic subject areas, not harm labels, and most
# subtopics span several distinct harms, so a coarse topic->category mapping is
# misleading. Instead each question was audited individually; the result is keyed
# here by (topic, subtopic, question), which is unique across the dataset.
_HARM_CATEGORY_MAP_FILE = Path(__file__).parent / "harmful_qa_harm_categories.json"


@lru_cache(maxsize=1)
def _load_harm_category_map() -> dict[tuple[str, str, str], str]:
    """
    Load and cache the bundled (topic, subtopic, question) -> canonical map.

    Returns:
        Mapping from a (topic, subtopic, question) tuple to a canonical HarmCategory name.

    Raises:
        ValueError: If the bundled map references a category that is not a canonical
            HarmCategory name.
    """
    valid_names = set(HarmCategory.__members__)
    with _HARM_CATEGORY_MAP_FILE.open(encoding="utf-8") as handle:
        records = json.load(handle)

    mapping: dict[tuple[str, str, str], str] = {}
    for record in records:
        category = record["category"]
        if category not in valid_names:
            raise ValueError(
                f"Bundled harmful_qa map contains unknown harm category {category!r}. "
                "Update harmful_qa_harm_categories.json to use canonical HarmCategory names."
            )
        mapping[(record["topic"], record["subtopic"], record["question"])] = category
    return mapping


class _HarmfulQADataset(_RemoteDatasetLoader):
    """
    Loader for the HarmfulQA dataset from HuggingFace.

    HarmfulQA contains approximately 2k harmful questions organized by academic topic
    and subtopic, designed to test LLM susceptibility to harm-inducing question-answering.

    References:
        - https://huggingface.co/datasets/declare-lab/HarmfulQA
        - [@bhardwaj2023harmfulqa]
    License: Apache 2.0

    Warning: This dataset contains harmful questions designed to test LLM safety.
    """

    HF_DATASET_NAME: str = "declare-lab/HarmfulQA"

    def __init__(
        self,
        *,
        split: str = "train",
    ) -> None:
        """
        Initialize the HarmfulQA dataset loader.

        Args:
            split: Dataset split to load. Defaults to "train".
        """
        self.split = split

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "harmful_qa"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch HarmfulQA dataset from HuggingFace and return as SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the HarmfulQA questions.
        """
        logger.info(f"Loading HarmfulQA dataset from {self.HF_DATASET_NAME}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.HF_DATASET_NAME,
            split=self.split,
            cache=cache,
        )

        authors = [
            "Rishabh Bhardwaj",
            "Soujanya Poria",
        ]
        description = (
            "HarmfulQA contains ~2k harmful questions organized by academic topic and subtopic, "
            "designed to test LLM susceptibility to harm-inducing question-answering. Introduced "
            "in 'Red-Teaming Large Language Models using Chain of Utterances for Safety Alignment' (2023)."
        )

        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        groups = ["DeCLaRe Lab, Singapore University of Technology and Design"]

        harm_category_map = _load_harm_category_map()
        unmapped = 0

        seed_prompts: list[SeedPrompt] = []
        for item in data:
            question = item["question"]
            topic = item.get("topic")
            subtopic = item.get("subtopic")

            category = harm_category_map.get((topic, subtopic, question))
            if category is not None:
                harm_categories = [category]
            else:
                # Row not present in the audited map (e.g. upstream added rows).
                # Fall back to the coarse subject mapping rather than mislabel.
                unmapped += 1
                harm_categories = self._standardize_harm_categories(topic)

            metadata: dict[str, str | int] = {}
            if topic:
                metadata["topic"] = topic
            if subtopic:
                metadata["subtopic"] = subtopic

            seed_prompts.append(
                SeedPrompt(
                    value=question,
                    data_type="text",
                    dataset_name=self.dataset_name,
                    harm_categories=harm_categories,
                    description=description,
                    source=source_url,
                    authors=authors,
                    groups=groups,
                    metadata=metadata,
                )
            )

        if unmapped:
            logger.warning(
                "%d HarmfulQA question(s) were not found in the bundled row-level harm-category "
                "map and fell back to the coarse topic mapping. The upstream dataset may have "
                "changed; regenerate harmful_qa_harm_categories.json to restore full coverage.",
                unmapped,
            )

        logger.info(f"Successfully loaded {len(seed_prompts)} questions from HarmfulQA dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
