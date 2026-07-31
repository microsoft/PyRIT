# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Literal

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedObjective
from pyrit.models.harm_category import HarmCategory


class _StrongRejectDataset(_RemoteDatasetLoader):
    """
    Loader for the StrongREJECT dataset.

    StrongREJECT is a refusal-robustness benchmark of 313 forbidden-behavior
    prompts spanning six harm categories. Each row also carries a ``source``
    column attributing prompts inherited from prior datasets (AdvBench, DAN,
    HarmfulQ, MaliciousInstruct, MasterKey, "Jailbreaking via Prompt
    Engineering", and the OpenAI System Card), with custom-authored StrongREJECT
    prompts marked ``source="custom"``.

    The companion 60-prompt subset published upstream
    (``strongreject_small_dataset.csv``) is intentionally not exposed: it is a
    strict prompt-subset of the full set with hand-edited metadata (three rows
    have their ``source`` rewritten to ``"custom"`` even though the same prompts
    are attributed to AdvBench/DAN in the full CSV). Users who want a smaller
    balanced sample can post-filter this loader at runtime.

    Reference: [@souly2024strongreject]
    """

    # Metadata
    harm_categories: list[str] = [
        "disinformation and deception",
        "hate, harassment and discrimination",
        "illegal goods and services",
        "non-violent crimes",
        "sexual content",
        "violence",
    ]
    modalities: list[str] = ["text"]
    size: str = "medium"  # 313 seeds
    tags: set[str] = {"jailbreak", "safety"}
    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = {
        "disinformation and deception": [HarmCategory.INFO_INTEGRITY, HarmCategory.DECEPTION],
        "hate, harassment and discrimination": [
            HarmCategory.HATE_SPEECH,
            HarmCategory.HARASSMENT,
            HarmCategory.REPRESENTATIONAL,
        ],
        "illegal goods and services": [HarmCategory.REGULATED_GOODS, HarmCategory.COORDINATION_HARM],
        "non-violent crimes": [HarmCategory.COORDINATION_HARM, HarmCategory.SCAMS],
        "sexual content": [HarmCategory.SEXUAL_CONTENT],
        "violence": [HarmCategory.VIOLENT_CONTENT, HarmCategory.VIOLENT_THREATS, HarmCategory.COORDINATION_HARM],
    }

    def __init__(
        self,
        *,
        source: str = (
            "https://raw.githubusercontent.com/alexandrasouly/strongreject/"
            "3432b2d696b428f242bd507df96d80f686571d5e/strongreject_dataset/strongreject_dataset.csv"
        ),
        source_type: Literal["public_url", "file"] = "public_url",
    ) -> None:
        """
        Initialize the StrongREJECT dataset loader.

        Args:
            source (str): URL to the StrongREJECT CSV file. Defaults to the pinned-commit raw URL
                on the upstream GitHub repository.
            source_type (Literal["public_url", "file"]): The type of source ('public_url' or 'file').
        """
        self.source = source
        self.source_type: Literal["public_url", "file"] = source_type

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "strong_reject"

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the StrongREJECT dataset and return it as a SeedDataset.

        Args:
            cache (bool): Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the StrongREJECT forbidden behaviors.

        Raises:
            ValueError: If any row is missing required keys or the dataset is empty.
        """
        required_keys = {"forbidden_prompt", "category", "source"}

        examples = self._fetch_from_url(
            source=self.source,
            source_type=self.source_type,
            cache=cache,
        )

        authors = [
            "Alexandra Souly",
            "Qingyuan Lu",
            "Dillon Bowen",
            "Tu Trinh",
            "Elvis Hsieh",
            "Sana Pandey",
            "Pieter Abbeel",
            "Justin Svegliato",
            "Scott Emmons",
            "Olivia Watkins",
            "Sam Toyer",
        ]

        seeds = []
        for example in examples:
            missing_keys = required_keys - example.keys()
            if missing_keys:
                raise ValueError(f"Missing keys in example: {', '.join(sorted(missing_keys))}")

            seeds.append(
                SeedObjective(
                    value=example["forbidden_prompt"],
                    name="StrongREJECT forbidden behavior",
                    dataset_name=self.dataset_name,
                    harm_categories=self._standardize_harm_categories(
                        example.get("category"),
                        alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES,
                    ),
                    description=(
                        "A StrongREJECT forbidden-behavior prompt covering one of six harm categories "
                        "(disinformation, hate/harassment/discrimination, illegal goods and services, "
                        "non-violent crimes, sexual content, violence). The companion StrongREJECT "
                        "rubric scores responses on refusal x convincingness x specificity."
                    ),
                    authors=authors,
                    groups=["UC Berkeley"],
                    source="https://github.com/alexandrasouly/strongreject",
                    metadata={"category": example["category"], "strong_reject_source": example["source"]},
                )
            )

        if not seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)
