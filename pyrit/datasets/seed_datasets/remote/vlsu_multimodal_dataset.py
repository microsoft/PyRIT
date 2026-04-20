# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import uuid
from enum import Enum
from typing import List, Literal, Optional

from pyrit.common.net_utility import make_request_and_raise_if_error_async
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt, data_serializer_factory

logger = logging.getLogger(__name__)


class VLSUCategory(Enum):
    """Categories in the ML-VLSU dataset."""

    SLURS_HATE_SPEECH = "C1: Slurs, Hate Speech, Hate Symbols"
    DISCRIMINATION = "C2: Discrimination and Unequal Treatment"
    DRUG_ABUSE = "C3: Drug Abuse"
    SELF_HARM = "C4: Self-Harm and Suicide"
    ANIMAL_VIOLENCE = "C5: Animal Violence and Gore"
    ADULT_EXPLICIT = "C6: Adult Explicit Sexual Material"
    ADULT_RACY = "C7: Adult Racy Material"
    WARFARE = "C8: Warfare and Armed Conflicts"
    INTERPERSONAL_VIOLENCE = "C9: Interpersonal Violence"
    WEAPONS = "C10: Weapons and Dangerous Objects"
    GORE_GRAPHIC = "C11: Gore and Graphic Content"
    TERRORISM = "C12: Terrorism and Violent Extremism"
    JAILBREAKS = "C13: Jailbreaks"
    INAUTHENTIC_PRACTICES = "C14: Inauthentic Practices/Fraud"
    HUMAN_EXPLOITATION = "C15: Human Exploitation"


class _VLSUMultimodalDataset(_RemoteDatasetLoader):
    """
    Loader for the ML-VLSU multimodal dataset.

    The ML-VLSU dataset contains image-text pairs with human annotations across three dimensions:
    image-only, text-only, and joint safety. This dataset is specifically designed to test
    emergent multimodal safety issues where harm arises from the combination of safe text
    and safe images.

    Note: The first call may be slow as images need to be downloaded from remote URLs.
    Subsequent calls will be faster since images are cached locally.

    Reference: https://arxiv.org/abs/2501.01151
    """

    def __init__(
        self,
        *,
        source: str = "https://raw.githubusercontent.com/apple/ml-vlsu/main/data/VLSU.csv",
        source_type: Literal["public_url", "file"] = "public_url",
        categories: Optional[List[VLSUCategory]] = None,
        unsafe_grades: Optional[List[str]] = ["unsafe", "borderline"],
        max_examples: Optional[int] = None,
    ):
        """
        Initialize the ML-VLSU multimodal dataset loader.

        Args:
            source: URL or file path to the VLSU CSV file. Defaults to official repository.
            source_type: The type of source ('public_url' or 'file').
            categories: List of VLSU categories to filter examples.
                If None, all categories are included (default).
            unsafe_grades: List of grades considered unsafe (e.g., ['unsafe', 'borderline']).
                Prompts are created only when the respective grade matches one of these values.
                Defaults to ['unsafe', 'borderline']. Possible options further include 'safe' and 'not_sure'.
            max_examples: Maximum number of multimodal examples to fetch. Each example produces
                2 prompts (text + image). If None, fetches all examples. Useful for testing
                or quick validations.

        Raises:
            ValueError: If any of the specified categories are invalid.
        """
        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type
        self.categories = categories
        self.unsafe_grades = unsafe_grades
        self.max_examples = max_examples

        # Validate categories if provided
        if categories is not None:
            valid_categories = {category.value for category in VLSUCategory}
            invalid_categories = (
                set(cat.value if isinstance(cat, VLSUCategory) else cat for cat in categories) - valid_categories
            )
            if invalid_categories:
                raise ValueError(f"Invalid VLSU categories: {', '.join(invalid_categories)}")

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "ml_vlsu"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch ML-VLSU multimodal examples and return as SeedDataset.

        Creates prompts based on safety grades:
        - Text prompt: if text_grade is 'unsafe' or 'borderline'
        - Image prompt: if image_grade is 'unsafe' or 'borderline'
        - Combined prompt: if combined_grade is 'unsafe' or 'borderline'

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the multimodal examples.

        Raises:
            ValueError: If any example is missing required keys.
        """
        logger.info(f"Loading ML-VLSU dataset from {self.source}")

        required_keys = {
            "prompt",
            "web_path",
            "combined_category",
            "uuid",
            "image_grade",
            "consensus_text_grade",
            "consensus_combined_grade",
            "image_category",
            "text_category",
        }
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

            text = example.get("prompt")
            image_url = example.get("web_path")

            # Get grades
            text_grade = example.get("consensus_text_grade", "").lower()
            image_grade = example.get("image_grade", "").lower()
            combined_grade = example.get("consensus_combined_grade", "").lower()

            # Get categories
            text_category = example.get("text_category")
            image_category = example.get("image_category")
            combined_category = example.get("combined_category")

            # Filter by categories if specified
            if self.categories is not None:
                category_values = {cat.value for cat in self.categories}
                if combined_category not in category_values:
                    continue

            # Only create prompts if combined_grade is unsafe or borderline
            if combined_grade not in self.unsafe_grades:
                continue

            # Generate a shared group_id for the text+image pair
            group_id = uuid.uuid4()

            try:
                local_image_path = await self._fetch_and_save_image_async(image_url, str(group_id))

                # Create text prompt (sequence=0, sent first)
                text_prompt = SeedPrompt(
                    value=text,
                    data_type="text",
                    name="ML-VLSU Text",
                    dataset_name=self.dataset_name,
                    harm_categories=[combined_category],
                    description="Text component of ML-VLSU multimodal prompt.",
                    source=self.source,
                    prompt_group_id=group_id,
                    sequence=0,
                    metadata={
                        "category": combined_category,
                        "text_grade": text_grade,
                        "image_grade": image_grade,
                        "combined_grade": combined_grade,
                    },
                )

                # Create image prompt (sequence=1, sent second)
                image_prompt = SeedPrompt(
                    value=local_image_path,
                    data_type="image_path",
                    name="ML-VLSU Image",
                    dataset_name=self.dataset_name,
                    harm_categories=[combined_category],
                    description="Image component of ML-VLSU multimodal prompt.",
                    source=self.source,
                    prompt_group_id=group_id,
                    sequence=1,
                    metadata={
                        "category": combined_category,
                        "text_grade": text_grade,
                        "image_grade": image_grade,
                        "combined_grade": combined_grade,
                        "original_image_url": image_url,
                    },
                )

                prompts.append(text_prompt)
                prompts.append(image_prompt)

                # Check if we've reached max_examples (each example = 2 prompts)
                if self.max_examples is not None and len(prompts) >= self.max_examples * 2:
                    break

            except Exception as e:
                failed_image_count += 1
                logger.warning(f"Failed to fetch image for combined prompt {group_id}: {e}")

        if failed_image_count > 0:
            logger.warning(f"[ML-VLSU] Skipped {failed_image_count} image(s) due to fetch failures")

        logger.info(f"Successfully loaded {len(prompts)} prompts from ML-VLSU dataset")

        return SeedDataset(seeds=prompts, dataset_name=self.dataset_name)

    async def _fetch_and_save_image_async(self, image_url: str, group_id: str) -> str:
        """
        Fetch and save an image from the ML-VLSU dataset.

        Args:
            image_url: URL to the image.
            group_id: Group ID for naming the cached file.

        Returns:
            Local path to the saved image.
        """
        filename = f"ml_vlsu_{group_id}.png"
        serializer = data_serializer_factory(category="seed-prompt-entries", data_type="image_path", extension="png")

        # Return existing path if image already exists
        serializer.value = str(serializer._memory.results_path + serializer.data_sub_directory + f"/{filename}")
        try:
            if await serializer._memory.results_storage_io.path_exists(serializer.value):
                return serializer.value
        except Exception as e:
            logger.warning(f"[ML-VLSU] Failed to check if image for {group_id} exists in cache: {e}")

        # Add browser-like headers for better success rate
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        response = await make_request_and_raise_if_error_async(
            endpoint_uri=image_url,
            method="GET",
            headers=headers,
            timeout=2.0,
            follow_redirects=True,
        )
        await serializer.save_data(data=response.content, output_filename=filename.replace(".png", ""))

        return str(serializer.value)
