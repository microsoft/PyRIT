# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared base for the garak (``garak-llm`` HuggingFace org) seed-dataset loaders.

The garak LLM vulnerability scanner pulls reference data from a family of
datasets hosted under https://huggingface.co/garak-llm. Each of those datasets is
flat: every row contributes a single string (a package name, a system prompt,
an audio clip), so the uniform mapping is **one row -> one ``SeedPrompt`` and one
HuggingFace repo -> one ``SeedDataset``**. A ``SeedPrompt`` is just a value plus
metadata, so a package name is as valid a ``SeedPrompt`` as a chat message.

Concrete loaders subclass ``_GarakRemoteDataset`` and set a handful of class
attributes (``HF_DATASET_NAME``, ``TEXT_COLUMN``, ``_DATASET_NAME``, optional
``METADATA_COLUMNS``); the base handles HuggingFace fetching, row -> ``SeedPrompt``
conversion, metadata preservation, and the empty-result guard.

Reference: [@derczynski2024garak]
"""

import logging
from abc import ABC
from typing import Any, ClassVar

from typing_extensions import override

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import _RemoteDatasetLoader
from pyrit.models import ChatMessageRole, PromptDataType, SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


class _GarakRemoteDataset(_RemoteDatasetLoader, ABC):
    """
    Abstract base for garak (``garak-llm``) HuggingFace seed datasets.

    Subclasses set the following class attributes:

    - ``HF_DATASET_NAME``: the ``garak-llm/<repo>`` HuggingFace identifier.
    - ``TEXT_COLUMN``: the column whose value becomes ``SeedPrompt.value``.
    - ``_DATASET_NAME``: the short ``garak_*`` name exposed via ``dataset_name``.
    - ``METADATA_COLUMNS`` (optional): extra columns preserved in
      ``SeedPrompt.metadata``. Maps the desired metadata key to one or more
      candidate source column names (the first present wins), so loaders can
      paper over upstream column-name drift (e.g. ``package_first_seen`` vs
      ``package first seen``).
    - ``ROLE`` (optional): ``SeedPrompt.role`` to assign (e.g. ``"system"``).

    Subclasses may also declare the class-level metadata attributes read by
    ``_parse_metadata_async`` (``tags``, ``size``, ``modalities``,
    ``harm_categories``).
    """

    should_register = False  # abstract base — concrete subclasses register themselves

    # Required per-dataset identifiers. Declared (not defaulted) so a subclass that
    # forgets to set them fails fast with AttributeError instead of silently using "".
    HF_DATASET_NAME: ClassVar[str]
    _DATASET_NAME: ClassVar[str]

    # Optional hooks with sensible family-wide defaults.
    TEXT_COLUMN: ClassVar[str] = "text"
    # Mapping of output metadata key -> candidate source column names (first match wins).
    METADATA_COLUMNS: ClassVar[dict[str, tuple[str, ...]]] = {}
    ROLE: ClassVar[ChatMessageRole | None] = None
    DATA_TYPE: ClassVar[PromptDataType] = "text"

    # Shared provenance metadata for the garak dataset family.
    SOURCE_AUTHORS: ClassVar[list[str]] = [
        "Leon Derczynski",
        "Erick Galinkin",
        "Jeffrey Martin",
        "Subho Majumdar",
        "Nanna Inie",
    ]
    SOURCE_GROUPS: ClassVar[list[str]] = [
        "NVIDIA",
        "IT University of Copenhagen",
        "University of Washington",
        "Vijil.ai",
    ]

    def __init__(self, *, max_examples: int | None = None) -> None:
        """
        Initialize the loader.

        Args:
            max_examples: Optional cap on the number of seeds to build. When
                None (the default) the full dataset is loaded; attacks that need
                the complete reference list rely on this. A small value is used
                by the integration tests to keep the multi-million-row package
                registries fast.
        """
        self._max_examples = max_examples

    @property
    def _source_url(self) -> str:
        """The canonical HuggingFace URL for this dataset."""
        return f"https://huggingface.co/datasets/{self.HF_DATASET_NAME}"

    @property
    @override
    def dataset_name(self) -> str:
        """The short garak dataset name."""
        return self._DATASET_NAME

    def _extract_metadata(self, item: dict[str, Any]) -> dict[str, Any]:
        """
        Build the per-seed metadata dict from a raw row.

        Args:
            item: A single raw HuggingFace row.

        Returns:
            dict[str, Any]: Metadata keyed per ``METADATA_COLUMNS``, skipping
                absent or null source columns.
        """
        metadata: dict[str, Any] = {}
        for out_key, candidates in self.METADATA_COLUMNS.items():
            for column in candidates:
                if column in item and item[column] is not None:
                    metadata[out_key] = item[column]
                    break
        return metadata

    def _build_seed(self, *, value: str, item: dict[str, Any]) -> SeedPrompt:
        """
        Construct a single ``SeedPrompt`` from a row's text value.

        Args:
            value: The text that becomes ``SeedPrompt.value``.
            item: The raw row, used to extract per-seed metadata.

        Returns:
            SeedPrompt: The constructed seed.
        """
        return SeedPrompt(
            value=value,
            data_type=self.DATA_TYPE,
            dataset_name=self.dataset_name,
            harm_categories=[],
            role=self.ROLE,
            source=self._source_url,
            authors=list(self.SOURCE_AUTHORS),
            groups=list(self.SOURCE_GROUPS),
            metadata=self._extract_metadata(item),
        )

    async def _fetch_rows_async(self, *, cache: bool) -> Any:
        """
        Fetch the raw HuggingFace ``train`` split for this dataset.

        Args:
            cache: Whether to cache the fetched dataset.

        Returns:
            The iterable HuggingFace dataset of raw rows.
        """
        return await self._fetch_from_huggingface_async(
            dataset_name=self.HF_DATASET_NAME,
            split="train",
            cache=cache,
        )

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch the garak dataset from HuggingFace and return it as a ``SeedDataset``.

        Each row's ``TEXT_COLUMN`` becomes a ``SeedPrompt.value``; rows whose text
        column is missing or empty are skipped.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True.

        Returns:
            SeedDataset: The garak dataset.

        Raises:
            ValueError: If no usable seeds remain after processing.
        """
        logger.info(f"Loading garak dataset {self.HF_DATASET_NAME}")
        data = await self._fetch_rows_async(cache=cache)

        seeds: list[SeedUnion] = []
        for item in data:
            value = item.get(self.TEXT_COLUMN)
            if value is None:
                continue
            value = str(value).strip()
            if not value:
                continue
            seeds.append(self._build_seed(value=value, item=item))
            if self._max_examples is not None and len(seeds) >= self._max_examples:
                break

        if not seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Successfully loaded {len(seeds)} seeds from {self.HF_DATASET_NAME}")
        return SeedDataset(seeds=seeds, dataset_name=self.dataset_name)
