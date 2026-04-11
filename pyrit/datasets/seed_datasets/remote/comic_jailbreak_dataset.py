# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Literal

from pyrit.common.net_utility import make_request_and_raise_if_error_async
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedObjective, data_serializer_factory

logger = logging.getLogger(__name__)

# Per-template bounding box coordinates and rotation from the paper's create_dataset.py
COMIC_JAILBREAK_TEMPLATES: dict[str, dict[str, tuple[int, int, int, int] | int]] = {
    "article": {"coord": (1080, 70, 1480, 680), "rotation": 0},
    "speech": {"coord": (1050, 40, 1500, 240), "rotation": 0},
    "instruction": {"coord": (1200, 130, 1420, 500), "rotation": 10},
    "message": {"coord": (1160, 120, 1400, 580), "rotation": 6},
    "code": {"coord": (1130, 210, 1490, 510), "rotation": 0},
}


class _ComicJailbreakDataset(_RemoteDatasetLoader):
    """
    Loader for the ComicJailbreak dataset.

    ComicJailbreak embeds harmful goals into 3-panel comic templates (article, speech,
    instruction, message, code) and prompts MLLMs to "complete the comic," achieving
    >90% ensemble ASR on Gemini and >85% on most open-source models.

    The dataset contains 300 harmful goals with template-specific text for each of
    the five comic template types.

    Reference: [@yu2025comicjailbreak]
    Paper: https://arxiv.org/abs/2603.21697
    Repository: https://github.com/Social-AI-Studio/ComicJailbreak
    """

    TEMPLATE_BASE_URL: str = (
        "https://raw.githubusercontent.com/Social-AI-Studio/ComicJailbreak/"
        "5fca32012ccac34dbd080df247926366249b4fb1/template/"
    )
    TEMPLATE_NAMES: tuple[str, ...] = ("article", "speech", "instruction", "message", "code")

    # Metadata
    harm_categories: tuple[str, ...] = (
        "harassment",
        "violence",
        "illegal",
        "malware",
        "misinformation",
        "sexual",
        "privacy",
    )
    modalities: tuple[str, ...] = ("text", "image")
    size: str = "large"  # 300 goals × 5 templates
    tags: frozenset[str] = frozenset({"safety", "multimodal"})

    def __init__(
        self,
        *,
        source: str = (
            "https://raw.githubusercontent.com/Social-AI-Studio/ComicJailbreak/"
            "7361c6cdbbff44331e5830a84b799476d354a968/dataset.csv"
        ),
        source_type: Literal["public_url", "file"] = "public_url",
    ):
        """
        Initialize the ComicJailbreak dataset loader.

        Args:
            source: URL to the ComicJailbreak CSV file. Defaults to the official repository
                at a pinned commit.
            source_type: The type of source ('public_url' or 'file').
        """
        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "comic_jailbreak"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch ComicJailbreak dataset and return as SeedDataset.

        Each row in the CSV becomes a SeedObjective whose value is the harmful goal.
        Template-specific text (Article, Speech, Instruction, Message, Code) and
        category/source metadata are stored in the seed's metadata dict.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the ComicJailbreak objectives.

        Raises:
            ValueError: If any example is missing the required 'Goal' key.
        """
        required_keys = {"Goal", "Category"}

        examples = self._fetch_from_url(
            source=self.source,
            source_type=self.source_type,
            cache=cache,
        )

        seeds: list[SeedObjective] = []
        template_columns = ["Article", "Speech", "Instruction", "Message", "Code"]

        for example in examples:
            missing_keys = required_keys - example.keys()
            if missing_keys:
                raise ValueError(f"Missing keys in example: {', '.join(missing_keys)}")

            goal = example["Goal"].strip()
            if not goal:
                logger.warning("[ComicJailbreak] Skipping entry with empty Goal")
                continue

            category = example.get("Category", "").strip()

            # Collect non-empty template texts as flat metadata keys
            template_metadata: dict[str, str] = {}
            for col in template_columns:
                text = example.get(col, "").strip()
                if text:
                    template_metadata[f"template_{col.lower()}"] = text

            seed = SeedObjective(
                value=goal,
                name="ComicJailbreak",
                dataset_name=self.dataset_name,
                harm_categories=[category] if category else [],
                description=(
                    "A harmful goal from the ComicJailbreak dataset designed to test "
                    "multimodal LLM safety via comic template jailbreaks."
                ),
                source="https://github.com/Social-AI-Studio/ComicJailbreak",
                authors=["Zhiyuan Yu", "Yuhao Wu", "Shengming Li", "Jiawei Xu", "Roy Ka-Wei Lee"],
                metadata={
                    "behavior": example.get("Behavior", ""),
                    "origin": example.get("Origin", ""),
                    **template_metadata,
                },
            )
            seeds.append(seed)

        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)

    async def fetch_template_async(self, template_name: str) -> str:
        """
        Fetch a comic template image from the remote repository with local caching.

        Args:
            template_name: One of 'article', 'speech', 'instruction', 'message', 'code'.

        Returns:
            str: Local file path to the cached template image.

        Raises:
            ValueError: If template_name is not a valid template.
        """
        if template_name not in self.TEMPLATE_NAMES:
            raise ValueError(
                f"Invalid template name '{template_name}'. Must be one of: {', '.join(self.TEMPLATE_NAMES)}"
            )

        filename = f"comic_jailbreak_{template_name}.png"
        serializer = data_serializer_factory(category="seed-prompt-entries", data_type="image_path", extension="png")

        serializer.value = str(serializer._memory.results_path + serializer.data_sub_directory + f"/{filename}")
        try:
            if await serializer._memory.results_storage_io.path_exists(serializer.value):
                return serializer.value
        except Exception as e:
            logger.warning(f"[ComicJailbreak] Failed to check cache for template {template_name}: {e}")

        image_url = f"{self.TEMPLATE_BASE_URL}{template_name}.png"
        response = await make_request_and_raise_if_error_async(endpoint_uri=image_url, method="GET")
        await serializer.save_data(data=response.content, output_filename=filename.replace(".png", ""))

        return str(serializer.value)
