# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset service for listing seed datasets and browsing their stored seed examples.

Wraps ``SeedDatasetProvider`` discovery and memory to list available datasets.
"""

import asyncio
import logging
import ntpath
import posixpath
import re
from collections.abc import Sequence
from fnmatch import translate
from functools import lru_cache
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pyrit.backend.mappers import format_last_message_preview
from pyrit.backend.mappers.attack_mappers import resolve_client_media_url_async
from pyrit.backend.models import (
    DatasetInfo,
    DatasetListResponse,
    PaginationInfo,
)
from pyrit.backend.models.datasets import (
    DatasetDeletionResponse,
    SeedExampleDetailResponse,
    SeedExampleListResponse,
    SeedExampleSummary,
    SeedMemberView,
)
from pyrit.common.pagination import DecodedKeysetCursor, decode_keyset_cursor, encode_keyset_cursor, fingerprint_filters
from pyrit.common.text_search import compile_like_pattern, validate_text_search
from pyrit.datasets import SeedDatasetProvider
from pyrit.exceptions.dataset_exception import DatasetAPIException
from pyrit.memory import CentralMemory
from pyrit.memory.dataset_lifecycle import DatasetNameConflictError
from pyrit.memory.dataset_mutation import editable_seed
from pyrit.models import (
    MEDIA_PATH_DATA_TYPES,
    ConversationStats,
    PromptDataType,
    SeedDatasetSummary,
    SeedRecord,
    SeedType,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pyrit.memory.dataset_mutation import DatasetEditingSnapshot


class DatasetService:
    """Service for listing seed datasets."""

    def __init__(self) -> None:
        """Initialize the dataset service."""
        self._memory = CentralMemory.get_memory_instance()

    async def delete_dataset_async(self, *, selection_key: str) -> DatasetDeletionResponse:
        """
        Permanently remove exact-name stored seeds and clean up their unshared owned media.

        Returns:
            DatasetDeletionResponse: Durable deletion and media-cleanup status.

        Raises:
            DatasetAPIException: If the selection is unnamed or has no stored seeds.
        """
        name = self._parse_selection_key(selection_key)
        if name is None:
            raise DatasetAPIException(status_code=400, message="The unnamed aggregate cannot be deleted.")
        try:
            deletion_id = await self._memory.delete_dataset_async(dataset_name=name)
        except LookupError as exc:
            raise DatasetAPIException(status_code=404, message="No stored dataset has this exact name.") from exc
        try:
            return await self.get_dataset_deletion_async(deletion_id)
        except Exception as exc:
            logger.exception("Dataset deletion cleanup interrupted: %s", deletion_id)
            raise DatasetAPIException(
                status_code=503,
                message="Seeds were deleted; cleanup requires a retry. Inspect the resource in Location.",
                cleanup_id=deletion_id,
            ) from exc

    async def get_dataset_deletion_async(self, deletion_id: UUID) -> DatasetDeletionResponse:
        """
        Read durable cleanup status and retry pending owned-media cleanup.

        Returns:
            DatasetDeletionResponse: The current cleanup outcome.

        Raises:
            DatasetAPIException: If the deletion identifier does not exist.
        """
        try:
            result = await self._memory.cleanup_dataset_media_async(operation_id=deletion_id)
        except DatasetNameConflictError as exc:
            raise DatasetAPIException(status_code=409, message="The upload is still active.") from exc
        except LookupError as exc:
            raise DatasetAPIException(status_code=404, message="Dataset deletion not found.") from exc
        return DatasetDeletionResponse.model_validate(result)

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
                    origins=list(summary.origins) if summary else [],
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
                    origins=list(unnamed_summary.origins),
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
        value_pattern: str | None = None,
        data_types: Sequence[PromptDataType] | None = None,
        harm_categories: Sequence[str] | None = None,
        seed_types: Sequence[SeedType] | None = None,
        editable: bool = False,
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
            value_pattern (str | None): A full case-insensitive LIKE pattern instead of search.
                '%' and '_' are wildcards; '/' escapes the next character.
            data_types (Sequence[PromptDataType] | None): Match examples with a member of any of these data types.
            harm_categories (Sequence[str] | None): Match examples with a member in any of these harm categories.
            seed_types (Sequence[SeedType] | None): Match examples with a member of any of these seed types.
            editable (bool): Include replacements and version from one exact-name stored snapshot.

        Returns:
            SeedExampleListResponse: The page, its pagination data, and the number of matching examples.

        Raises:
            ValueError: If the selection key or the cursor is not valid.
        """
        dataset_name = self._parse_selection_key(selection_key)
        validate_text_search(value=search, pattern=value_pattern)
        fingerprint = fingerprint_filters(
            filters={
                "selection_key": selection_key,
                "data_types": data_types or None,
                "harm_categories": [category.lower() for category in harm_categories] if harm_categories else None,
                "seed_types": seed_types or None,
                "search": search or None,
                "value_pattern": value_pattern,
            }
        )
        after = decode_keyset_cursor(cursor=cursor, fingerprint=fingerprint)
        if cursor and after is None:
            raise ValueError("The cursor is not valid for this dataset and these filters")

        version = None
        if editable:
            snapshot = await self._editing_snapshot_async(dataset_name)
            version = snapshot.version
            examples, total, next_after = self._editing_page(
                records=snapshot.records,
                limit=limit,
                after=after,
                data_types=data_types,
                harm_categories=harm_categories,
                seed_types=seed_types,
                search=search,
                value_pattern=value_pattern,
            )
        else:
            examples, total, next_after = await self._memory.get_seed_examples_async(
                dataset_name=dataset_name,
                limit=limit,
                after=after,
                data_types=data_types,
                harm_categories=harm_categories,
                seed_types=seed_types,
                value_search=search,
                value_pattern=value_pattern,
            )
        next_cursor = (
            encode_keyset_cursor(
                timestamp=next_after.timestamp, identifier=next_after.identifier, fingerprint=fingerprint
            )
            if next_after
            else None
        )
        items = [self._summarize(example_id=example_id, seeds=seeds) for example_id, seeds in examples.items()]
        if version is not None:
            for item in items:
                item.editable_seeds = await asyncio.to_thread(
                    self._editable_seeds, records=examples[item.example_id], version=version
                )
        return SeedExampleListResponse(
            items=items,
            version=version,
            pagination=PaginationInfo(
                limit=limit, has_more=next_after is not None, next_cursor=next_cursor, prev_cursor=cursor
            ),
            total=total,
        )

    async def get_seed_example_async(
        self, *, selection_key: str, example_id: UUID, editable: bool = False
    ) -> SeedExampleDetailResponse | None:
        """
        Get one stored logical seed example of a dataset with all of its members.

        Args:
            selection_key (str): The ``selection_key`` of a dataset from ``list_datasets_async``.
            example_id (UUID): The ``example_id`` from the list response.
            editable (bool): Include replacements and version from one exact-name stored snapshot.

        Returns:
            SeedExampleDetailResponse | None: The example, or None if the dataset does not contain it.

        Raises:
            ValueError: If the selection key is not valid.
        """
        name = self._parse_selection_key(selection_key)
        version = None
        if editable:
            snapshot = await self._editing_snapshot_async(name)
            version = snapshot.version
            seeds = sorted(
                [record for record in snapshot.records if (record.prompt_group_id or record.id) == example_id],
                key=lambda record: (
                    record.seed_type != "objective",
                    record.sequence is not None,
                    record.sequence or 0,
                    str(record.id),
                ),
            )
        else:
            seeds = await self._memory.get_seed_example_async(dataset_name=name, example_id=example_id)
        if not seeds:
            return None
        summary = self._summarize(example_id=example_id, seeds=seeds)
        members = [await self._seed_member_view_async(seed) for seed in seeds]
        if version is not None:
            summary.editable_seeds = await asyncio.to_thread(self._editable_seeds, records=seeds, version=version)
        return SeedExampleDetailResponse(**summary.model_dump(), members=members, version=version)

    async def _editing_snapshot_async(self, dataset_name: str | None) -> "DatasetEditingSnapshot":
        if dataset_name is None:
            raise DatasetAPIException(status_code=400, message="The unnamed aggregate cannot be edited.")
        try:
            return await self._memory.get_dataset_editing_snapshot_async(dataset_name)
        except LookupError as exc:
            raise DatasetAPIException(status_code=404, message="No stored dataset has this exact name.") from exc
        except (ValueError, TypeError) as exc:
            raise DatasetAPIException(status_code=422, message="A stored seed is not safely submit-ready.") from exc

    @staticmethod
    def _editable_seeds(*, records: Sequence[SeedRecord], version: str) -> list[dict[str, Any]]:
        try:
            return [editable_seed(record=record, version=version) for record in records]
        except (ValueError, TypeError) as exc:
            raise DatasetAPIException(
                status_code=422, message="A stored seed is not submit-ready; repair its configuration."
            ) from exc

    @staticmethod
    def _editing_pattern(pattern: str | None) -> re.Pattern[str] | None:
        if pattern is None:
            return None
        characters = iter(compile_like_pattern(pattern).lower())
        glob = ""
        for character in characters:
            if character == "/":
                character = next(characters)
            elif character == "%":
                glob += "*"
                continue
            elif character == "_":
                glob += "?"
                continue
            glob += f"[{character}]" if character in "*?[" else character
        return re.compile(translate(glob))

    @staticmethod
    def _editing_page(
        *,
        records: Sequence[SeedRecord],
        limit: int,
        after: DecodedKeysetCursor | None,
        data_types: Sequence[PromptDataType] | None,
        harm_categories: Sequence[str] | None,
        seed_types: Sequence[SeedType] | None,
        search: str | None,
        value_pattern: str | None,
    ) -> tuple[dict[UUID, list[SeedRecord]], int, DecodedKeysetCursor | None]:
        pattern = DatasetService._editing_pattern(value_pattern)
        groups: dict[UUID, list[SeedRecord]] = {}
        for record in records:
            groups.setdefault(record.prompt_group_id or record.id, []).append(record)
        matched = {
            identifier: members
            for identifier, members in groups.items()
            if (not data_types or any(seed.data_type in data_types for seed in members))
            and (not seed_types or any(seed.seed_type in seed_types for seed in members))
            and (
                not harm_categories
                or any(
                    category.lower() in {value.lower() for value in harm_categories}
                    for seed in members
                    for category in seed.harm_categories or []
                )
            )
            and (
                not search
                or any(
                    seed.data_type == "text"
                    and seed.seed_type != "simulated_conversation"
                    and search.casefold() in seed.value.casefold()
                    for seed in members
                )
            )
            and (
                pattern is None
                or any(
                    seed.data_type == "text"
                    and seed.seed_type != "simulated_conversation"
                    and pattern.fullmatch(seed.value.lower()) is not None
                    for seed in members
                )
            )
        }
        keys = {
            identifier: (min(seed.date_added for seed in members), str(identifier))
            for identifier, members in matched.items()
        }
        ordered = sorted(matched, key=lambda identifier: keys[identifier], reverse=True)
        if after is not None:
            ordered = [identifier for identifier in ordered if keys[identifier] < (after.timestamp, after.identifier)]
        page = {
            identifier: sorted(
                matched[identifier],
                key=lambda seed: (
                    seed.seed_type != "objective",
                    seed.sequence is not None,
                    seed.sequence or 0,
                    str(seed.id),
                ),
            )
            for identifier in ordered[:limit]
        }
        last = ordered[limit - 1] if len(ordered) > limit else None
        next_after = DecodedKeysetCursor(timestamp=keys[last][0], identifier=str(last)) if last is not None else None
        return page, len(matched), next_after

    @staticmethod
    async def _seed_member_view_async(seed: SeedRecord) -> SeedMemberView:
        """
        Attach a display URL without changing the stored seed value.

        List rows do not call this. Media bytes are resolved only when one example is opened.

        Returns:
            The stored seed plus ``value_url`` for media, or null for everything else.
        """
        value_url = await resolve_client_media_url_async(value=seed.value, data_type=seed.data_type)
        return SeedMemberView.model_validate({**seed.model_dump(), "value_url": value_url})

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
            origins=tuple(sorted({origin for summary in unnamed for origin in summary.origins})),
        )


@lru_cache(maxsize=1)
def get_dataset_service() -> DatasetService:
    """
    Get the global dataset service instance.

    Returns:
        The singleton DatasetService instance.
    """
    return DatasetService()
