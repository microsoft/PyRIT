# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import uuid
from enum import Enum
from typing import Literal, Optional

from pyrit.common.net_utility import make_request_and_raise_if_error_async
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt, data_serializer_factory

logger = logging.getLogger(__name__)


class SemanticCategory(Enum):
    """Semantic categories in the HarmBench multimodal dataset."""

    CYBERCRIME_INTRUSION = "cybercrime_intrusion"  # n=54
    ILLEGAL = "illegal"  # 36
    HARMFUL = "harmful"  # 9
    CHEMICAL_BIOLOGICAL = "chemical_biological"  # 4
    HARASSMENT_BULLYING = "harassment_bullying"  # 4
    MISINFORMATION_DISINFORMATION = "misinformation_disinformation"  # 3


class _HarmBenchMultimodalDataset(_RemoteDatasetLoader):
    """
    Loader for the HarmBench multimodal dataset.

    The HarmBench multimodal dataset contains 110 harmful behaviors.
    Each example consists of an image ("image_path") and a behavior string referencing the image ("text").
    The text and image prompts that belong to the same example are linked using the same prompt_group_id.

    Note: The first call may be slow as images need to be downloaded from the remote repository.
    Subsequent calls will be faster since images are cached locally.

    Reference: https://www.harmbench.org/
    Paper: https://arxiv.org/abs/2402.04249
    """

    def __init__(
        self,
        *,
        source: str = (
            "https://raw.githubusercontent.com/centerforaisafety/HarmBench/c0423b9/data/behavior_datasets/"
            "harmbench_behaviors_multimodal_all.csv"
        ),
        source_type: Literal["public_url", "file"] = "public_url",
        categories: Optional[list[SemanticCategory]] = None,
    ):
        """
        Initialize the HarmBench multimodal dataset loader.

        Args:
            source: URL or file path to the HarmBench CSV file. Defaults to official repository.
            source_type: The type of source ('public_url' or 'file').
            categories: List of semantic categories to filter examples.
                If None, all categories are included (default).

        Raises:
            ValueError: If any of the specified categories are invalid.
        """
        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type
        self.categories = categories

        # Validate categories if provided
        if categories is not None:
            valid_categories = {category.value for category in SemanticCategory}
            invalid_categories = {
                cat.value if isinstance(cat, SemanticCategory) else cat for cat in categories
            } - valid_categories
            if invalid_categories:
                raise ValueError(f"Invalid semantic categories: {', '.join(invalid_categories)}")

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "harmbench_multimodal"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch HarmBench multimodal examples and return as SeedDataset.

        The dataset contains both image and text prompts linked by prompt_group_id.
        You can extract the grouped prompts using the group_seed_prompts_by_prompt_group_id method.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the multimodal examples.

        Raises:
            ValueError: If any example is missing required keys.
        """
        logger.info(f"Loading HarmBench multimodal dataset from {self.source}")

        required_keys = {"Behavior", "BehaviorID", "FunctionalCategory", "SemanticCategory", "ImageFileName"}
        examples = self._fetch_from_url(
            source=self.source,
            source_type=self.source_type,
            cache=cache,
        )

        prompts = []
        failed_image_count = 0

        for example in examples:
            missing_keys = required_keys - example.keys()
            if missing_keys:
                raise ValueError(f"Missing keys in example: {', '.join(missing_keys)}")

            if example["FunctionalCategory"] != "multimodal":
                continue

            semantic_category = example["SemanticCategory"]

            # Filter by categories if specified
            if self.categories is not None:
                category_values = {cat.value for cat in self.categories}
                if semantic_category not in category_values:
                    continue

            behavior_text = example["Behavior"]
            behavior_id = example["BehaviorID"]
            image_filename = example["ImageFileName"]
            image_description = example.get("ImageDescription", "")
            redacted_description = example.get("RedactedImageDescription", "")

            # A unique group ID to link the text and image prompts
            group_id = uuid.uuid4()

            # All images in HarmBench are stored as .png files
            image_url = (
                "https://raw.githubusercontent.com/centerforaisafety/HarmBench/c0423b9/data/multimodal_behavior_images/"
                f"{image_filename.rsplit('.', 1)[0]}.png"
            )

            try:
                # Only include examples where image fetch is successful
                local_image_path = await self._fetch_and_save_image_async(image_url, behavior_id)
            except Exception as e:
                failed_image_count += 1
                logger.warning(
                    f"[HarmBench-Multimodal] Failed to fetch image for behavior {behavior_id}: {e}. "
                    f"Skipping this example."
                )
                continue

            # Image fetch succeeded - add both image and text prompts
            image_prompt = SeedPrompt(
                value=local_image_path,
                data_type="image_path",
                name=f"HarmBench Multimodal Image - {behavior_id}",
                dataset_name=self.dataset_name,
                harm_categories=[semantic_category],
                description=f"An image prompt from the HarmBench multimodal dataset, BehaviorID: {behavior_id}",
                source=self.source,
                prompt_group_id=group_id,
                sequence=0,
                metadata={
                    "behavior_id": behavior_id,
                    "image_description": image_description,
                    "redacted_image_description": redacted_description,
                    "original_image_url": image_url,
                },
            )
            prompts.append(image_prompt)

            text_prompt = SeedPrompt(
                value=behavior_text,
                data_type="text",
                name=f"HarmBench Multimodal Text - {behavior_id}",
                dataset_name=self.dataset_name,
                harm_categories=[semantic_category],
                description=f"A text prompt from the HarmBench multimodal dataset, BehaviorID: {behavior_id}",
                source=self.source,
                prompt_group_id=group_id,
                sequence=0,
                metadata={
                    "behavior_id": behavior_id,
                },
                authors=[
                    "Mantas Mazeika",
                    "Long Phan",
                    "Xuwang Yin",
                    "Andy Zou",
                    "Zifan Wang",
                    "Norman Mu",
                    "Elham Sakhaee",
                    "Nathaniel Li",
                    "Steven Basart",
                    "Bo Li",
                    "David Forsyth",
                    "Dan Hendrycks",
                ],
                groups=[
                    "University of Illinois Urbana-Champaign",
                    "Center for AI Safety",
                    "Carnegie Mellon University",
                    "UC Berkeley",
                    "Microsoft",
                ],
            )
            prompts.append(text_prompt)

        if failed_image_count > 0:
            logger.warning(
                f"[HarmBench-Multimodal] Skipped {failed_image_count} example(s) due to image fetch failures"
            )

        logger.info(f"Successfully loaded {len(prompts)} prompts from HarmBench multimodal dataset")

        return SeedDataset(seeds=prompts, dataset_name=self.dataset_name)

    async def _fetch_and_save_image_async(self, image_url: str, behavior_id: str) -> str:
        """
        Fetch and save an image from the HarmBench repository.

        Args:
            image_url: URL to the image.
            behavior_id: Behavior ID for naming the cached file.

        Returns:
            Local path to the saved image.
        """
        filename = f"harmbench_{behavior_id}.png"
        serializer = data_serializer_factory(category="seed-prompt-entries", data_type="image_path", extension="png")

        # Return existing path if image already exists for this BehaviorID
        serializer.value = str(serializer._memory.results_path + serializer.data_sub_directory + f"/{filename}")
        try:
            if await serializer._memory.results_storage_io.path_exists(serializer.value):
                return serializer.value
        except Exception as e:
            logger.warning(f"[HarmBench-Multimodal] Failed to check if image for {behavior_id} exists in cache: {e}")

        response = await make_request_and_raise_if_error_async(endpoint_uri=image_url, method="GET")
        await serializer.save_data(data=response.content, output_filename=filename.replace(".png", ""))

        return str(serializer.value)
