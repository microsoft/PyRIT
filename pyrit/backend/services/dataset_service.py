# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset service for listing seed datasets and browsing their stored seed examples.

Wraps ``SeedDatasetProvider`` discovery and memory to list available datasets.
"""

import logging
import ntpath
import posixpath
import re
from collections.abc import Sequence
from functools import lru_cache
from uuid import UUID

from pyrit.backend.mappers import format_last_message_preview
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.datasets import (
    DatasetInfo,
    DatasetListResponse,
    SeedExampleDetailResponse,
    SeedExampleListResponse,
    SeedExampleSummary,
)
from pyrit.common.pagination import decode_keyset_cursor, encode_keyset_cursor, fingerprint_filters
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.models import (
    MEDIA_PATH_DATA_TYPES,
    ConversationStats,
    PromptDataType,
    SeedDatasetSummary,
    SeedRecord,
    SeedType,
)

logger = logging.getLogger(__name__)


class DatasetService:
    """Service for listing seed datasets."""

    def __init__(self) -> None:
        """Initialize the dataset service."""
        self._memory = CentralMemory.get_memory_instance()

    async def list_datasets_async(self, *, loaded_only: bool = False) -> DatasetListResponse:
        """
        List all available datasets.

        By default the response combines datasets discoverable via registered providers
        with those already loaded into memory, which is what the legacy name-list endpoint
        returned. Pass ``loaded_only=True`` to restrict the response to the memory-backed
        population; an empty memory then returns a valid empty response (#2746) whether or
        not providers are registered.

        Empty dataset names are never surfaced as a named choice: a seed with an empty
        name filters nothing in the seed queries, so selecting it would match seeds from
        every dataset. Seeds stored under an empty name are folded into the unnamed
        population instead. Named and unnamed selections live in distinct namespaces so a
        stored dataset named ``__unnamed__`` cannot collide with the unnamed population.

        Args:
            loaded_only (bool): When True, return only datasets that have seeds in memory.

        Returns:
            DatasetListResponse: Available datasets.
        """
        provider_names = {name for name in await SeedDatasetProvider.get_all_dataset_names_async() if name}
        summaries = await self._memory.get_seed_dataset_summaries_async()
        named_summaries = {summary.dataset_name: summary for summary in summaries if summary.dataset_name}

        dataset_names = set(named_summaries) if loaded_only else provider_names | set(named_summaries)

        items: list[DatasetInfo] = []
        for dataset_name in sorted(dataset_names):
            summary = named_summaries.get(dataset_name)
            items.append(
                DatasetInfo(
                    name=dataset_name,
                    selection_key=f"dataset:named:{dataset_name}",
                    loaded=summary is not None,
                    provider_available=dataset_name in provider_names,
                    logical_examples=summary.logical_examples if summary else None,
                    seed_pieces=summary.seed_pieces if summary else None,
                    objectives=summary.objectives if summary else None,
                    modalities=list(summary.modalities) if summary else [],
                    harm_categories=list(summary.harm_categories) if summary else [],
                    has_unlabeled_harm_categories=summary.has_unlabeled_harm_categories if summary else False,
                )
            )

        unnamed_summary = self._merge_unnamed_summaries(summaries)
        if unnamed_summary is not None:
            items.append(
                DatasetInfo(
                    name="(unnamed)",
                    selection_key="dataset:unnamed",
                    loaded=True,
                    provider_available=False,
                    logical_examples=unnamed_summary.logical_examples,
                    seed_pieces=unnamed_summary.seed_pieces,
                    objectives=unnamed_summary.objectives,
                    modalities=list(unnamed_summary.modalities),
                    harm_categories=list(unnamed_summary.harm_categories),
                    has_unlabeled_harm_categories=unnamed_summary.has_unlabeled_harm_categories,
                )
            )

        return DatasetListResponse(items=items)

    async def list_seed_examples_async(
        self,
        *,
        selection_key: str,
        limit: int = 20,
        cursor: str | None = None,
        search: str | None = None,
        data_types: Sequence[PromptDataType] | None = None,
        harm_categories: Sequence[str] | None = None,
        seed_types: Sequence[SeedType] | None = None,
    ) -> SeedExampleListResponse:
        """
        List one page of the stored logical seed examples of a dataset.

        The cursor is bound to the dataset and the filters. A cursor that is not valid for the
        request causes an error. It does not restart at the first page.

        Args:
            selection_key (str): The ``selection_key`` of a dataset from ``list_datasets_async``.
            limit (int): The maximum number of examples to return.
            cursor (str | None): The ``next_cursor`` of the previous page.
            search (str | None): Literal text that the value of a text prompt or objective must contain,
                ignoring case. Simulated-conversation configurations are not searched.
            data_types (Sequence[PromptDataType] | None): Match examples with a member of any of these data types.
            harm_categories (Sequence[str] | None): Match examples with a member in any of these harm categories.
            seed_types (Sequence[SeedType] | None): Match examples with a member of any of these seed types.

        Returns:
            SeedExampleListResponse: The page, its pagination data, and the number of matching examples.

        Raises:
            ValueError: If the selection key or the cursor is not valid.
        """
        dataset_name = self._parse_selection_key(selection_key)
        fingerprint = fingerprint_filters(
            filters={
                "selection_key": selection_key,
                "data_types": data_types or None,
                "harm_categories": [category.lower() for category in harm_categories] if harm_categories else None,
                "seed_types": seed_types or None,
                "search": search or None,
            }
        )
        after = decode_keyset_cursor(cursor=cursor, fingerprint=fingerprint)
        if cursor and after is None:
            raise ValueError("The cursor is not valid for this dataset and these filters")

        examples, total, next_after = await self._memory.get_seed_examples_async(
            dataset_name=dataset_name,
            limit=limit,
            after=after,
            data_types=data_types,
            harm_categories=harm_categories,
            seed_types=seed_types,
            value_search=search,
        )
        next_cursor = (
            encode_keyset_cursor(
                timestamp=next_after.timestamp, identifier=next_after.identifier, fingerprint=fingerprint
            )
            if next_after
            else None
        )
        return SeedExampleListResponse(
            items=[self._summarize(example_id=example_id, seeds=seeds) for example_id, seeds in examples.items()],
            pagination=PaginationInfo(
                limit=limit, has_more=next_after is not None, next_cursor=next_cursor, prev_cursor=cursor
            ),
            total=total,
        )

    async def get_seed_example_async(self, *, selection_key: str, example_id: UUID) -> SeedExampleDetailResponse | None:
        """
        Get one stored logical seed example of a dataset with all of its members.

        Args:
            selection_key (str): The ``selection_key`` of a dataset from ``list_datasets_async``.
            example_id (UUID): The ``example_id`` from the list response.

        Returns:
            SeedExampleDetailResponse | None: The example, or None if the dataset does not contain it.

        Raises:
            ValueError: If the selection key is not valid.
        """
        seeds = await self._memory.get_seed_example_async(
            dataset_name=self._parse_selection_key(selection_key), example_id=example_id
        )
        if not seeds:
            return None
        summary = self._summarize(example_id=example_id, seeds=seeds)
        return SeedExampleDetailResponse(**summary.model_dump(), members=seeds)

    @staticmethod
    def _parse_selection_key(selection_key: str) -> str | None:
        """
        Get the dataset name of a selection key.

        Returns:
            str | None: The dataset name, or None for the unnamed population.

        Raises:
            ValueError: If the selection key is not valid.
        """
        if selection_key == "dataset:unnamed":
            return None
        name = selection_key.removeprefix("dataset:named:")
        if not name or name == selection_key:
            raise ValueError(f"Invalid dataset selection key: {selection_key}")
        return name

    @classmethod
    def _summarize(cls, *, example_id: UUID, seeds: Sequence[SeedRecord]) -> SeedExampleSummary:
        preview, truncated = cls._preview(seeds)
        return SeedExampleSummary(
            example_id=example_id,
            name=next((seed.name for seed in seeds if seed.name), None),
            preview=preview,
            preview_truncated=truncated,
            modalities=sorted({seed.data_type for seed in seeds}),
            seed_types=sorted({seed.seed_type for seed in seeds}),
            piece_count=len(seeds),
            objective_count=sum(seed.seed_type == "objective" for seed in seeds),
            harm_categories=sorted({category for seed in seeds for category in seed.harm_categories or []}),
            has_unlabeled_harm=any(not seed.harm_categories for seed in seeds),
        )

    @staticmethod
    def _preview(seeds: Sequence[SeedRecord]) -> tuple[str, bool]:
        """
        Build the list preview from the first text seed, or else from a type label.

        A simulated-conversation configuration is not a prompt, so it gets only a label.
        Media seeds show only a file name. Standalone paths and URLs stored as text get a label.

        Returns:
            tuple[str, bool]: The preview and whether the text was shortened.
        """
        shown = [seed for seed in seeds if seed.seed_type != "simulated_conversation"]
        seed = next((seed for seed in shown if seed.data_type == "text"), shown[0] if shown else None)
        if seed is None:
            return "[Simulated conversation configuration]", False
        value = seed.value.lstrip()
        if seed.data_type == "text" and (
            ntpath.isabs(value) or posixpath.isabs(value) or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value)
        ):
            return "[Text reference]", False
        preview = None
        if seed.data_type == "text" or seed.data_type in MEDIA_PATH_DATA_TYPES:
            preview = format_last_message_preview(value=seed.value, data_type=seed.data_type)
        truncated = seed.data_type == "text" and len(seed.value) > ConversationStats.PREVIEW_MAX_LEN
        return preview or f"[{seed.data_type}]", truncated

    @staticmethod
    def _merge_unnamed_summaries(summaries: Sequence[SeedDatasetSummary]) -> SeedDatasetSummary | None:
        """
        Fold the memory groups that filter nothing (``None`` and empty names) into one entry.

        Such seeds behave identically in the seed queries, so the summary represents them
        as a single unnamed population and never as a named choice.

        Args:
            summaries (Sequence[SeedDatasetSummary]): The memory-backed dataset summaries.

        Returns:
            SeedDatasetSummary | None: One merged summary, or None when no unnamed seeds
                are stored.
        """
        unnamed = [summary for summary in summaries if not summary.dataset_name]
        if not unnamed:
            return None
        return SeedDatasetSummary(
            dataset_name=None,
            logical_examples=sum(summary.logical_examples for summary in unnamed),
            seed_pieces=sum(summary.seed_pieces for summary in unnamed),
            objectives=sum(summary.objectives for summary in unnamed),
            modalities=tuple(sorted({modality for summary in unnamed for modality in summary.modalities})),
            harm_categories=tuple(sorted({category for summary in unnamed for category in summary.harm_categories})),
            has_unlabeled_harm_categories=any(summary.has_unlabeled_harm_categories for summary in unnamed),
        )


@lru_cache(maxsize=1)
def get_dataset_service() -> DatasetService:
    """
    Get the global dataset service instance.

    Returns:
        The singleton DatasetService instance.
    """
    return DatasetService()
