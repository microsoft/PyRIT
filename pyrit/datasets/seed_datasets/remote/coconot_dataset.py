# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedObjective

if TYPE_CHECKING:
    from pyrit.models.seeds.seed_group import SeedUnion

logger = logging.getLogger(__name__)


class CoCoNotCategory(Enum):
    """
    The 5 top-level noncompliance categories defined in the CoCoNot taxonomy.

    Values match the casing used by the upstream HuggingFace dataset so they can be
    used as direct row-filter keys.
    """

    INCOMPLETE = "Incomplete requests"
    UNSUPPORTED = "Unsupported requests"
    INDETERMINATE = "Indeterminate requests"
    HUMANIZING = "Humanizing requests"
    SAFETY = "Requests with safety concerns"


class CoCoNotSplit(Enum):
    """Splits available for the upstream ``original`` config."""

    TRAIN = "train"
    TEST = "test"


class _CoCoNotBaseDataset(_RemoteDatasetLoader):
    """
    Shared base for the two CoCoNot sibling loaders.

    CoCoNot (Contextual Noncompliance) is an evaluation suite for refusal calibration in
    LLMs. The dataset is split across two configs that this base wraps with sibling
    subclasses:

    - ``original`` (train+test): prompts the model SHOULD refuse, drawn from 5
      noncompliance categories (incomplete, unsupported, indeterminate, humanizing,
      safety).
    - ``contrast`` (test only): benign look-alike prompts the model SHOULD comply with,
      used to measure over-refusal behavior.

    Subclasses set ``CONFIG``, ``SPLITS``, ``DEFAULT_DESCRIPTION``, ``size``, and a
    ``dataset_name`` property.

    References:
        - https://huggingface.co/datasets/allenai/coconot
        - https://github.com/allenai/noncompliance
        - [@brahman2024coconot]

    License: ODC-BY 1.0.
    """

    _AUTHORS: ClassVar[list[str]] = [
        "Faeze Brahman",
        "Sachin Kumar",
        "Vidhisha Balachandran",
        "Pradeep Dasigi",
        "Valentina Pyatkin",
        "Abhilasha Ravichander",
        "Sarah Wiegreffe",
        "Nouha Dziri",
        "Khyathi Chandu",
        "Jack Hessel",
        "Yulia Tsvetkov",
        "Noah A. Smith",
        "Yejin Choi",
        "Hannaneh Hajishirzi",
    ]

    _GROUPS: ClassVar[list[str]] = [
        "Allen Institute for Artificial Intelligence",
        "University of Washington",
        "The Ohio State University",
        "Microsoft Research",
        "Samaya AI",
        "NVIDIA",
    ]

    HF_DATASET_NAME: str = "allenai/coconot"

    CONFIG: str
    SPLITS: tuple[str, ...]
    DEFAULT_DESCRIPTION: str

    harm_categories: list[str] = [m.value.lower() for m in CoCoNotCategory]
    modalities: list[str] = ["text"]
    tags: set[str] = {"safety"}

    def __init__(self, *, categories: list[CoCoNotCategory] | None = None) -> None:
        """
        Initialize the CoCoNot base loader.

        Args:
            categories (list[CoCoNotCategory] | None): Subset of noncompliance categories
                to include. ``None`` (default) loads all 5 categories.

        Raises:
            ValueError: If any value in ``categories`` is not a CoCoNotCategory.
        """
        if categories is not None:
            if not categories:
                raise ValueError("`categories` must be a non-empty list (pass None to include all categories)")
            self._validate_enums(values=categories, enum_cls=CoCoNotCategory, label="categories")
        self._categories = categories

    def _resolved_splits(self) -> tuple[str, ...]:
        """
        Return the splits to iterate when fetching.

        Subclasses with a user-facing splits filter override this to read the filter.
        Base implementation returns the class-level ``SPLITS`` tuple unchanged.

        Returns:
            tuple[str, ...]: The split names to load.
        """
        return self.SPLITS

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the CoCoNot subset and return it as a SeedDataset.

        Iterates ``self._resolved_splits()`` and calls the inherited
        ``_fetch_from_huggingface_async`` once per split, then filters by
        ``self._categories`` if set.

        Args:
            cache (bool): Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: SeedDataset of SeedObjectives. Each seed carries per-row
            metadata: ``id``, ``category``, ``subcategory``, ``subset`` (HF config name),
            ``split``, and ``response`` (populated only on ``original.train`` rows).

        Raises:
            ValueError: If no rows match the category filter.
        """
        wanted_categories = {c.value for c in self._categories} if self._categories else None
        source_url = f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"
        seeds: list[SeedUnion] = []

        for split in self._resolved_splits():
            logger.info(f"Loading CoCoNot rows (config={self.CONFIG}, split={split})")
            rows = await self._fetch_from_huggingface_async(
                dataset_name=self.HF_DATASET_NAME,
                config=self.CONFIG,
                split=split,
                cache=cache,
            )
            for row in rows:
                category = row.get("category")
                if wanted_categories is not None and category not in wanted_categories:
                    continue
                # The upstream HF dataset contains a small number of rows with an
                # empty ``prompt`` (observed in original.train under the wildchats
                # subcategory). SeedObjective enforces value != "" downstream, so
                # skip them here to keep the loader resilient to upstream drift.
                if not (row.get("prompt") or "").strip():
                    logger.warning(
                        f"Skipping CoCoNot row with empty prompt "
                        f"(id={row.get('id')!r}, category={category!r}, split={split!r})"
                    )
                    continue
                seeds.append(self._row_to_seed(row=row, split=split, source_url=source_url))

        if not seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Successfully loaded {len(seeds)} objectives from CoCoNot ({self.dataset_name})")
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)

    def _row_to_seed(self, *, row: dict[str, Any], split: str, source_url: str) -> SeedObjective:
        """
        Convert one HF row into a SeedObjective with full per-row metadata.

        Args:
            row (dict): One row from the HuggingFace dataset.
            split (str): The split this row came from (used as ``metadata["split"]``).
            source_url (str): Canonical source URL for the dataset.

        Returns:
            SeedObjective: The constructed seed.
        """
        category = row.get("category") or ""
        metadata: dict[str, str | int] = {
            "id": row.get("id", ""),
            "category": category,
            "subcategory": row.get("subcategory", ""),
            "subset": self.CONFIG,
            "split": split,
        }
        # response is populated in original.train and empty in test splits.
        response = row.get("response")
        if response:
            metadata["response"] = response

        # CoCoNot's noncompliance taxonomy (incomplete/unsupported/indeterminate/
        # humanizing/safety) is not a harm taxonomy, so harm categories are left
        # empty while the native category stays in metadata.
        harm_categories: list[str] = []

        return SeedObjective(
            value=row["prompt"],
            dataset_name=self.dataset_name,
            harm_categories=harm_categories,
            description=self.DEFAULT_DESCRIPTION,
            source=source_url,
            authors=self._AUTHORS,
            groups=self._GROUPS,
            metadata=metadata,
        )


class _CoCoNotRefusalDataset(_CoCoNotBaseDataset):
    """
    12,478 prompts (train+test) the model SHOULD refuse.

    Maps to the ``original`` config of ``allenai/coconot``. Combines the ``train`` split
    (11,477 rows, each carrying AI2's reference noncompliant response in
    ``metadata["response"]``) and the ``test`` split (1,001 rows, no reference response).

    Use the ``splits`` constructor argument to restrict to one split.

    Reference: [@brahman2024coconot]
    """

    CONFIG: str = "original"
    SPLITS: tuple[str, ...] = ("train", "test")
    size: str = "huge"
    DEFAULT_DESCRIPTION: str = (
        "CoCoNot refusal-target set — 12,478 prompts the model should NOT comply with, "
        "drawn from 5 noncompliance categories (incomplete, unsupported, indeterminate, "
        "humanizing, safety). Combines the `original.train` (11,477) and `original.test` "
        "(1,001) splits of `allenai/coconot`."
    )

    def __init__(
        self,
        *,
        categories: list[CoCoNotCategory] | None = None,
        splits: list[CoCoNotSplit] | None = None,
    ) -> None:
        """
        Initialize the CoCoNot refusal-target loader.

        Args:
            categories (list[CoCoNotCategory] | None): Subset of noncompliance categories
                to include. ``None`` (default) loads all 5 categories.
            splits (list[CoCoNotSplit] | None): Subset of upstream splits to load. ``None``
                (default) loads both ``train`` (11,477 rows) and ``test`` (1,001 rows).

        Raises:
            ValueError: If any value in ``categories`` or ``splits`` is the wrong enum
                type.
        """
        super().__init__(categories=categories)
        if splits is not None:
            if not splits:
                raise ValueError("`splits` must be a non-empty list (pass None to include all splits)")
            self._validate_enums(values=splits, enum_cls=CoCoNotSplit, label="splits")
        self._splits = splits

    @override
    def _resolved_splits(self) -> tuple[str, ...]:
        """
        Return the splits to load, honoring the user-supplied ``splits`` filter.

        Returns:
            tuple[str, ...]: ``self.SPLITS`` when no filter is set, otherwise the
            user-selected subset in the order they passed.
        """
        if self._splits is None:
            return self.SPLITS
        return tuple(s.value for s in self._splits)

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "coconot_refusal"


class _CoCoNotContrastDataset(_CoCoNotBaseDataset):
    """
    379 look-alike benign prompts the model SHOULD comply with.

    Maps to the ``contrast.test`` config/split of ``allenai/coconot``. Used to measure
    over-refusal behavior: each prompt is superficially similar to a refusal-target
    prompt but is in fact benign.

    Reference: [@brahman2024coconot]
    """

    CONFIG: str = "contrast"
    SPLITS: tuple[str, ...] = ("test",)
    size: str = "medium"
    tags: set[str] = {"safety", "refusal"}
    DEFAULT_DESCRIPTION: str = (
        "CoCoNot contrast set — 379 benign prompts that look superficially similar to "
        "refusal-target prompts but should be complied with. Used to measure "
        "over-refusal behavior. Maps to the `contrast.test` config of "
        "`allenai/coconot`."
    )

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "coconot_contrast"
