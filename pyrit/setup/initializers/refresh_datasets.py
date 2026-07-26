# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Refresh datasets already loaded into memory.

Re-fetches datasets that are present in ``CentralMemory`` from their registered providers and
replaces their stored seeds, so previously loaded copies pick up upstream changes such as
standardized harm categories, corrected metadata, or live threat-feed updates. This is the
maintenance twin of ``LoadDefaultDatasets``: it is opt-in and never runs on the scenario hot path.
"""

import logging
import textwrap
from datetime import datetime, timedelta, timezone

from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import SeedDataset
from pyrit.models.parameter import Parameter
from pyrit.setup.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


class RefreshDatasets(PyRITInitializer):
    """
    Refresh datasets already loaded in memory from their registered providers.

    For each selected dataset that is present in memory and backed by a registered provider, this
    re-fetches the dataset with caching disabled and replaces its stored seeds. Selection can be
    narrowed with ``dataset_names``; a ``days`` threshold limits the refresh to datasets whose
    newest seed is older than ``days`` days (``days=0`` refreshes every selected dataset regardless
    of age).

    Datasets in memory that have no registered provider (for example custom, manually added ones)
    are skipped, since there is nothing to re-fetch.
    """

    DEFAULT_DAYS: int = 30
    ADDED_BY: str = "RefreshDatasets"

    @property
    def description(self) -> str:
        """A description of this initializer."""
        return textwrap.dedent(
            """
                Refreshes datasets already present in memory by re-fetching them from their
                registered providers with caching disabled and replacing their stored seeds. Use
                days to refresh only datasets whose newest seed is older than N days (days=0
                refreshes all selected datasets); use dataset_names to narrow the selection.

                Note: this is intended for periodic maintenance, not the scenario hot path. It only
                refreshes datasets that are already in memory and backed by a registered provider.
            """
        ).strip()

    @property
    def required_env_vars(self) -> list[str]:
        """The list of required environment variables."""
        return []

    @property
    def supported_parameters(self) -> list[Parameter]:
        """The list of parameters this initializer accepts."""
        return [
            Parameter(
                name="days",
                description=(
                    "Refresh only datasets whose newest seed is older than this many days. "
                    "0 refreshes every selected dataset regardless of age."
                ),
                default=self.DEFAULT_DAYS,
            ),
            Parameter(
                name="dataset_names",
                description="Explicit dataset names to refresh; refreshes all in-memory datasets if omitted.",
                default=[],
            ),
        ]

    async def initialize_async(self) -> None:
        """Refresh the selected stale datasets in CentralMemory, isolating per-dataset failures."""
        days = self._parse_days()
        memory = CentralMemory.get_memory_instance()

        names_in_memory = set(memory.get_seed_dataset_names())
        if not names_in_memory:
            logger.warning("No datasets in memory to refresh")
            return

        candidates = await self._select_candidates_async(names_in_memory=names_in_memory)
        if not candidates:
            logger.warning("No datasets matched the requested selection")
            return

        refreshed: list[str] = []
        up_to_date: list[str] = []
        failed: list[str] = []
        for name in candidates:
            if not self._is_stale(memory=memory, dataset_name=name, days=days):
                up_to_date.append(name)
                continue
            try:
                await self._refresh_dataset_async(memory=memory, dataset_name=name)
                refreshed.append(name)
            except Exception as exc:  # noqa: BLE001 - isolate one dataset's failure from the rest
                logger.warning(f"Skipping refresh for dataset '{name}': {exc}")
                failed.append(name)

        logger.info(f"Refresh complete: {len(refreshed)} refreshed, {len(up_to_date)} up-to-date, {len(failed)} failed")

    async def _select_candidates_async(self, *, names_in_memory: set[str]) -> list[str]:
        """
        Resolve which in-memory datasets to consider for refresh.

        With explicit ``dataset_names``, only those are considered; otherwise every in-memory
        dataset that has a registered provider is considered. Names that are not in memory or have
        no registered provider are skipped with a log message.

        Args:
            names_in_memory (set[str]): The dataset names currently present in memory.

        Returns:
            list[str]: The dataset names to evaluate for staleness.
        """
        dataset_names = self.params.get("dataset_names", [])
        registered = set(await SeedDatasetProvider.get_all_dataset_names_async())

        if dataset_names:
            candidates: list[str] = []
            for name in dict.fromkeys(dataset_names):
                if name not in names_in_memory:
                    logger.warning(f"Skipping '{name}': not present in memory")
                elif name not in registered:
                    logger.warning(f"Skipping '{name}': no registered provider to refresh from")
                else:
                    candidates.append(name)
            return candidates

        selected: list[str] = []
        for name in sorted(names_in_memory):
            if name in registered:
                selected.append(name)
            else:
                logger.debug(f"Skipping '{name}': no registered provider to refresh from")
        return selected

    def _is_stale(self, *, memory: MemoryInterface, dataset_name: str, days: int) -> bool:
        """
        Determine whether a dataset is stale enough to refresh.

        Args:
            memory (MemoryInterface): The memory instance to read existing seeds from.
            dataset_name (str): The dataset to evaluate.
            days (int): The staleness threshold in days; 0 always refreshes.

        Returns:
            bool: True if the dataset should be refreshed, otherwise False.
        """
        if days == 0:
            return True

        seeds = memory.get_seeds(dataset_name=dataset_name)
        newest = max((seed.date_added for seed in seeds if seed.date_added is not None), default=None)
        if newest is None:
            return True

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        return newest <= cutoff

    async def _refresh_dataset_async(self, *, memory: MemoryInterface, dataset_name: str) -> None:
        """
        Re-fetch a single dataset and atomically replace its stored seeds.

        The dataset is fetched (with caching disabled) before anything is deleted, and the replace
        is a single transaction, so a failed or empty fetch - or a failed insert - leaves the
        existing seeds untouched.

        Args:
            memory (MemoryInterface): The memory instance to replace seeds in.
            dataset_name (str): The dataset to refresh.

        Raises:
            ValueError: If the provider returns no usable dataset for ``dataset_name``.
        """
        fetched = await SeedDatasetProvider.fetch_datasets_async(
            dataset_names=[dataset_name], cache=False, max_concurrency=1
        )
        dataset = self._require_matching_dataset(fetched=fetched, dataset_name=dataset_name)

        deleted = await memory.replace_seeds_for_dataset_async(
            dataset_name=dataset_name, seeds=dataset.seeds, added_by=self.ADDED_BY
        )
        logger.info(f"Refreshed dataset '{dataset_name}': replaced {deleted} seeds with {len(dataset.seeds)}")

    def _parse_days(self) -> int:
        """
        Parse and validate the ``days`` parameter.

        Returns:
            int: The validated non-negative staleness threshold.

        Raises:
            ValueError: If ``days`` is not a single non-negative integer.
        """
        raw = self.params.get("days", [])
        if not raw:
            return self.DEFAULT_DAYS
        if len(raw) != 1:
            raise ValueError(f"'days' must be a single non-negative integer, got {raw}")
        try:
            days = int(raw[0])
        except (TypeError, ValueError):
            raise ValueError(f"'days' must be a non-negative integer, got {raw[0]!r}") from None
        if days < 0:
            raise ValueError(f"'days' must be non-negative, got {days}")
        return days

    @staticmethod
    def _require_matching_dataset(*, fetched: list[SeedDataset], dataset_name: str) -> SeedDataset:
        """
        Validate a fetch returned exactly the requested, non-empty dataset before replacing seeds.

        Guards the destructive replace against a provider that returns nothing, more than one
        dataset, an empty dataset, or a dataset whose seeds carry a different ``dataset_name`` than
        requested (which would delete the requested dataset and insert unrelated seeds).

        Args:
            fetched (list[SeedDataset]): The datasets returned by the provider.
            dataset_name (str): The dataset name that was requested.

        Returns:
            SeedDataset: The single fetched dataset that matches ``dataset_name``.

        Raises:
            ValueError: If the fetch did not return exactly one non-empty dataset for the
                requested name.
        """
        if len(fetched) != 1:
            raise ValueError(f"Expected exactly one dataset for '{dataset_name}', got {len(fetched)}")
        dataset = fetched[0]
        if not dataset.seeds:
            raise ValueError(f"Re-fetched dataset '{dataset_name}' is empty; keeping existing seeds")
        mismatched = sorted(
            {seed.dataset_name for seed in dataset.seeds if seed.dataset_name != dataset_name},
            key=lambda name: (name is None, name or ""),
        )
        if mismatched:
            raise ValueError(
                f"Re-fetched dataset for '{dataset_name}' contains seeds for other datasets "
                f"{mismatched}; keeping existing seeds"
            )
        return dataset
