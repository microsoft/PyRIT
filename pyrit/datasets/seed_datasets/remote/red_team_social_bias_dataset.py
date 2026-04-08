# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import logging
from uuid import uuid4

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)


class _RedTeamSocialBiasDataset(_RemoteDatasetLoader):
    """
    Loader for the Red Team Social Bias Prompts dataset.

    This dataset contains aggregated and unified existing red-teaming prompts
    designed to identify stereotypes, discrimination, hate speech, and other
    representation harms in text-based Large Language Models (LLMs).

    Reference: [@vantaylor2024socialbias]
    """

    def __init__(
        self,
        *,
        source: str = "svannie678/red_team_repo_social_bias_prompts",
    ):
        """
        Initialize the Red Team Social Bias dataset loader.

        Args:
            source: HuggingFace dataset identifier. Defaults to "svannie678/red_team_repo_social_bias_prompts".
        """
        self.source = source

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "red_team_social_bias"

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch Red Team Social Bias dataset and return as SeedDataset.

        This dataset contains 3 prompt types: "Single Prompt", "Multi Turn" and
        "Multi Turn, Single Prompt". Multi-turn prompts are linked by prompt_group_id.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the red team social bias prompts.
        """
        logger.info(f"Loading Red Team Social Bias dataset from {self.source}")

        data = await self._fetch_from_huggingface(
            dataset_name=self.source,
            config="default",
            split="train",
            cache=cache,
        )

        common_metadata = {
            "dataset_name": self.dataset_name,
            "authors": ["Simone van Taylor"],
            "description": (
                "This dataset contains aggregated and unified existing red-teaming prompts "
                "designed to identify stereotypes, discrimination, hate speech, and other "
                "representation harms in text-based Large Language Models (LLMs)."
            ),
            "source": f"https://huggingface.co/datasets/{self.source}",
        }

        seed_prompts = []

        for item in data:
            prompt_type = item.get("prompt_type")

            if prompt_type is None:
                continue

            # Dictionary of metadata for the current prompt
            prompt_metadata = {
                **common_metadata,
                "harm_categories": (
                    [item["categorization"]]
                    if not isinstance(item.get("categorization"), list)
                    else item.get("categorization", [])
                ),
                "groups": [item.get("organization", "")],
                "metadata": {
                    "prompt_type": prompt_type,
                },
            }

            if prompt_type in ["Multi Turn"]:
                # Get the prompt value - try different keys
                prompt_data = item.get("prompt", item.get("Prompt", ""))
                if not prompt_data:  # Skip if no prompt data
                    continue

                # Safely parse the user prompts, remove the unwanted ones such as "assistant" and "system"
                user_prompts = [
                    turn["body"] for turn in ast.literal_eval(prompt_data) if turn["role"].startswith("user")
                ]

                group_id = uuid4()
                for i, user_prompt in enumerate(user_prompts):
                    seed_prompts.append(
                        SeedPrompt(
                            value=user_prompt,
                            data_type="text",
                            prompt_group_id=group_id,
                            sequence=i,
                            **prompt_metadata,  # type: ignore[arg-type]
                        )
                    )
            else:
                # Get the prompt value - try different keys
                prompt_value = item.get("prompt", item.get("Prompt", ""))
                if not prompt_value:  # Skip empty prompts
                    continue

                # Clean up single turn prompts that contain unwanted lines of text
                cleaned_value = prompt_value.replace("### Response:", "").replace("### Instruction:", "").strip()
                # some entries have contents that trip up jinja2, so we escape them
                escaped_cleaned_value = cleaned_value
                seed_prompts.append(
                    SeedPrompt(
                        value=escaped_cleaned_value,
                        data_type="text",
                        **prompt_metadata,  # type: ignore[arg-type]
                    )
                )

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from Red Team Social Bias dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)
