# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from typing import Any

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import Modality, SeedDataset, SeedPrompt, SeedUnion
from pyrit.models.harm_category import HarmCategory

logger = logging.getLogger(__name__)

HF_DATASET_NAME: str = "stuckdavis/speciesist-bias-prompts"
# Commit SHA of the v0.2.0 tag on the Hugging Face dataset repository.
HF_REVISION: str = "6e0cfa08f4b88de3aef80ad9f0015072901f60dc"
HF_SOURCE_URL: str = f"https://huggingface.co/datasets/{HF_DATASET_NAME}"

_AUTHORS: list[str] = ["Sam Tucker-Davis"]

_REQUIRED_KEYS: tuple[str, ...] = ("id", "prompt", "prompt_type", "species", "harm_category")


class _SpeciesistBiasDataset(_RemoteDatasetLoader):
    """
    Loader for the Speciesist Bias Prompts dataset.

    Prompts for checking whether a model treats the interests of different animal
    species inconsistently or dismissively. Most rows are comparative prompts that
    name two species in one prompt, so a single response can show uneven treatment.
    A smaller set of split pairs fills one template with one species at a time; the
    two halves share a ``pair_id`` in metadata and are emitted as independent seeds.
    Each row also carries a ``framing`` (``implicit`` everyday task or ``explicit``
    question) and a ``bias_signal``: one sentence on what a biased answer would look
    like, which a scorer or an attack objective can use.

    The dataset is for checking inconsistency and dismissiveness, not diet advice.

    PyRIT has no species-bias harm category, so every seed uses ``HarmCategory.OTHER``
    and keeps the source label ``speciesist_bias`` in ``metadata["harm_category"]``.
    This loader does not implement a scorer.

    References:
        - Dataset card: [@tuckerdavis2026speciesistbias]
        - HuggingFace: https://huggingface.co/datasets/stuckdavis/speciesist-bias-prompts

    License: CC BY 4.0.
    """

    HF_DATASET_NAME: str = HF_DATASET_NAME
    HF_REVISION: str = HF_REVISION

    HARM_CATEGORY_ALIAS_OVERRIDES: dict[str, list[HarmCategory]] = {
        "speciesist_bias": [HarmCategory.OTHER],
    }

    # Metadata
    modalities: tuple[Modality, ...] = (Modality.TEXT,)
    size: str = "medium"  # 77 comparative prompts and 16 split pairs (109 rows)
    tags: frozenset[str] = frozenset({"safety", "bias", "ethics"})
    harm_categories: list[str] = ["speciesist_bias"]

    _DESCRIPTION: str = (
        "Prompts naming one or two animal species, for checking whether a model treats similar "
        "interests of different species inconsistently or dismissively. For checking inconsistency "
        "and dismissiveness, not diet advice."
    )

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "speciesist_bias"

    @override
    async def _fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the Speciesist Bias Prompts dataset and return it as a SeedDataset.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: One SeedPrompt per row.

        Raises:
            ValueError: If a row is missing a required field or the dataset is empty.
        """
        logger.info(f"Loading Speciesist Bias Prompts dataset from {self.HF_DATASET_NAME}@{self.HF_REVISION}")

        data = await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            split="train",
            cache=cache,
            revision=self.HF_REVISION,
        )

        seed_prompts: list[SeedUnion] = [self._build_seed_prompt(row=dict(row)) for row in data]

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty. The Speciesist Bias Prompts dataset returned no rows.")

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from Speciesist Bias Prompts dataset")

        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _build_seed_prompt(self, *, row: dict[str, Any]) -> SeedPrompt:
        """
        Build a SeedPrompt from one dataset row.

        Args:
            row (dict[str, Any]): A raw dataset row.

        Returns:
            SeedPrompt: The constructed seed prompt.

        Raises:
            ValueError: If the row is missing a required field.
        """
        missing = [key for key in _REQUIRED_KEYS if row.get(key) in (None, "", [])]
        if missing:
            raise ValueError(f"Speciesist Bias Prompts row {row.get('id')!r} is missing required field(s): {missing}")

        metadata: dict[str, str | list[str]] = {
            "item_id": str(row["id"]),
            "harm_category": str(row["harm_category"]),
            "prompt_type": str(row["prompt_type"]),
            "species": [str(s) for s in row["species"]],
            "contexts": [str(c) for c in row.get("contexts") or []],
            "task": str(row.get("task") or ""),
            "framing": str(row.get("framing") or ""),
            "bias_signal": str(row.get("bias_signal") or ""),
            "source": str(row.get("source") or ""),
            "version": str(row.get("version") or ""),
        }
        if row.get("pair_id"):
            metadata["pair_id"] = str(row["pair_id"])

        return SeedPrompt(
            value=str(row["prompt"]),
            data_type="text",
            dataset_name=self.dataset_name,
            harm_categories=self._standardize_harm_categories(
                str(row["harm_category"]), alias_overrides=self.HARM_CATEGORY_ALIAS_OVERRIDES
            ),
            description=self._DESCRIPTION,
            source=HF_SOURCE_URL,
            authors=_AUTHORS,
            metadata=metadata,
        )
