# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset service for listing seed datasets.

Wraps ``SeedDatasetProvider`` discovery and memory to list available datasets.
"""

import logging
from collections.abc import Sequence
from functools import lru_cache

from pyrit.backend.models.datasets import (
    DatasetInfo,
    DatasetListResponse,
)
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory
from pyrit.models import SeedDatasetSummary

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
        summaries = self._memory.get_seed_dataset_summaries()
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
