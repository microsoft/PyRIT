# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from enum import Enum
from typing import Any, ClassVar

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)

_HF_REPO_ID = "allenai/wildjailbreak"

# Both WildJailbreak configs ship their rows under a single HuggingFace split named "train",
# including the ``eval`` config.
_HF_SPLIT = "train"


class WildJailbreakSplit(Enum):
    """Which WildJailbreak config to fetch."""

    EVAL = "eval"
    TRAIN = "train"


class WildJailbreakDataType(Enum):
    """The ``data_type`` of a WildJailbreak row."""

    VANILLA_HARMFUL = "vanilla_harmful"
    VANILLA_BENIGN = "vanilla_benign"
    ADVERSARIAL_HARMFUL = "adversarial_harmful"
    ADVERSARIAL_BENIGN = "adversarial_benign"


class _WildJailbreakDataset(_RemoteDatasetLoader):
    """
    Loader for the WildJailbreak dataset (Allen Institute for AI).

    WildJailbreak is a synthetic safety dataset. Each plain ("vanilla") request is paired
    with an adversarial rewrite that applies jailbreak tactics mined from in-the-wild user
    chats, so the adversarial rows are ready-to-send jailbreak attempts. Benign look-alike
    prompts are included for checking over-refusal.

    The dataset ships two configs:

    - ``eval`` (2,210 rows): 2,000 ``adversarial_harmful`` and 210 ``adversarial_benign``
      rows. Columns: ``adversarial``, ``label``, ``data_type``.
    - ``train`` (~261,559 rows, a ~530 MB download): all four data types. Columns:
      ``vanilla``, ``adversarial``, ``completion``, ``data_type``.

    By default this loader fetches the ``eval`` config and keeps only the
    ``adversarial_harmful`` rows (2,000 prompts). The ``train`` config must be requested
    explicitly.

    For adversarial rows the seed value is the ``adversarial`` text; for vanilla rows it is
    the ``vanilla`` text. The ``completion`` column (model responses used as training
    targets) is never loaded.

    Gating: the dataset is gated under the AI2 Responsible Use Guidelines. To use this
    loader you must accept the terms on the HuggingFace dataset page and supply a HF
    access token (either via the ``token`` constructor argument or the
    ``HUGGINGFACE_TOKEN`` environment variable).

    Warning: the corpus contains adversarial prompts on sensitive topics (violence,
    self-harm, weapons, drugs, etc.). Reader / target discretion is recommended.

    License: Open Data Commons Attribution License (ODC-BY 1.0). Access is also
    gated behind the AI2 Responsible Use Guidelines click-through on HuggingFace
    (a separate access control, not the data license).
    Reference: [@jiang2024wildteaming]
    HuggingFace: https://huggingface.co/datasets/allenai/wildjailbreak
    """

    _AUTHORS: ClassVar[list[str]] = [
        "Liwei Jiang",
        "Kavel Rao",
        "Seungju Han",
        "Allyson Ettinger",
        "Faeze Brahman",
        "Sachin Kumar",
        "Niloofar Mireshghallah",
        "Ximing Lu",
        "Maarten Sap",
        "Yejin Choi",
        "Nouha Dziri",
    ]
    _GROUPS: ClassVar[list[str]] = [
        "Allen Institute for AI",
        "University of Washington",
        "Carnegie Mellon University",
    ]
    HF_DATASET_NAME: str = _HF_REPO_ID
    modalities: list[str] = ["text"]
    size: str = "large"  # 2,000 prompts with the default filters
    tags: set[str] = {"safety", "jailbreak"}

    def __init__(
        self,
        *,
        split: WildJailbreakSplit = WildJailbreakSplit.EVAL,
        data_types: list[WildJailbreakDataType] | None = None,
        token: str | None = None,
    ) -> None:
        """
        Initialize the WildJailbreak dataset loader.

        Args:
            split (WildJailbreakSplit): Which config to fetch. Defaults to
                ``WildJailbreakSplit.EVAL``. ``WildJailbreakSplit.TRAIN`` is a ~530 MB download.
            data_types (list[WildJailbreakDataType] | None): Keep only rows whose ``data_type``
                is in this list. Defaults to ``[WildJailbreakDataType.ADVERSARIAL_HARMFUL]``.
                The ``eval`` config has no vanilla rows.
            token (str | None): HuggingFace authentication token. If not provided,
                reads from ``HUGGINGFACE_TOKEN`` env var.

        Raises:
            ValueError: If ``split`` or any ``data_types`` value is not a valid enum member,
                or if ``data_types`` is an empty list.
        """
        self._validate_enum(split, WildJailbreakSplit, "split")
        self.split = split
        self.data_types = self._resolve_data_types(data_types)
        self.token = token if token is not None else os.environ.get("HUGGINGFACE_TOKEN")
        self.source = f"https://huggingface.co/datasets/{_HF_REPO_ID}"

    @property
    def dataset_name(self) -> str:
        """The dataset name."""
        return "wildjailbreak"

    async def _fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch WildJailbreak and return as a SeedDataset.

        Args:
            cache (bool): Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: A SeedDataset containing the filtered WildJailbreak prompts.

        Raises:
            ValueError: If the filter combination produces zero seeds.
        """
        logger.info(
            f"Loading WildJailbreak dataset (split={self.split.value}, "
            f"data_types={[dt.value for dt in self.data_types]})"
        )

        rows = await self._fetch_from_huggingface_async(
            dataset_name=_HF_REPO_ID,
            config=self.split.value,
            split=_HF_SPLIT,
            cache=cache,
            token=self.token,
            delimiter="\t",
            keep_default_na=False,
        )
        seed_prompts = self._rows_to_seeds(rows=rows)

        if not seed_prompts:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Successfully loaded {len(seed_prompts)} prompts from WildJailbreak dataset")
        return SeedDataset(seeds=seed_prompts, dataset_name=self.dataset_name)

    def _rows_to_seeds(self, *, rows: Any) -> list[SeedPrompt]:
        """
        Convert raw HuggingFace rows into filtered SeedPrompts.

        Args:
            rows (Any): The HuggingFace dataset object for the selected config.

        Returns:
            list[SeedPrompt]: SeedPrompts that survived the ``data_types`` filter.
        """
        kept_data_types = {dt.value for dt in self.data_types}

        seeds: list[SeedPrompt] = []
        for row in rows:
            data_type = row.get("data_type")
            if data_type not in kept_data_types:
                continue

            is_adversarial = data_type.startswith("adversarial")
            prompt_text = row.get("adversarial") if is_adversarial else row.get("vanilla")
            # A few train rows have an empty adversarial rewrite; there is nothing to send.
            if not prompt_text:
                continue

            metadata: dict[str, Any] = {"split": self.split.value, "data_type": data_type}
            if is_adversarial and row.get("vanilla"):
                metadata["vanilla"] = row["vanilla"]
            if row.get("label") not in (None, ""):
                metadata["label"] = row["label"]

            seeds.append(
                SeedPrompt(
                    value=prompt_text,
                    data_type="text",
                    dataset_name=self.dataset_name,
                    source=self.source,
                    authors=self._AUTHORS,
                    groups=self._GROUPS,
                    metadata=metadata,
                )
            )
        return seeds

    @staticmethod
    def _resolve_data_types(data_types: list[WildJailbreakDataType] | None) -> list[WildJailbreakDataType]:
        """
        Validate and normalize the requested data types.

        Args:
            data_types (list[WildJailbreakDataType] | None): User-supplied list, or None.

        Returns:
            list[WildJailbreakDataType]: Normalized list (defaults to ``[ADVERSARIAL_HARMFUL]``).

        Raises:
            ValueError: If ``data_types`` is an empty list or contains non-enum values.
        """
        if data_types is None:
            return [WildJailbreakDataType.ADVERSARIAL_HARMFUL]
        if not data_types:
            raise ValueError(
                "WildJailbreak data_types must not be empty. Pass None to use the default "
                "[ADVERSARIAL_HARMFUL], or supply at least one WildJailbreakDataType value."
            )
        _RemoteDatasetLoader._validate_enums(data_types, WildJailbreakDataType, "data_type")
        return list(data_types)
