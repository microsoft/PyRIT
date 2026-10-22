# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Datasets a scenario needs in order to build its attacks.

Some scenarios assemble their attack population from datasets used as ingredients
(templates, registries, partial keys). Those datasets are not the population a user
selects or limits: they are always read in full, without source caps, sampling, or
filters. ``ScenarioRequiredData`` declares them apart from ``dataset_config``, which then
only describes and limits the assembled attack groups.

Preparation reuses ``DatasetConfiguration``, so required data is fetched, stored, and
refreshed with the same guarantees as any dataset source. Reads never fetch.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pyrit.models.scenario_dataset import (
    DatasetFetchPolicy,
    DatasetSelectionMode,
    ScenarioDatasetOptions,
    ScenarioDatasetRequest,
    ScenarioDatasetSourceSettings,
)
from pyrit.scenario.core.dataset_configuration import (
    DatasetConfiguration,
    DatasetConstraintError,
    DatasetSource,
    ResolvedDataset,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pyrit.models import Seed


class _RequiredDatasetConfiguration(DatasetConfiguration):
    """An uncapped, unfiltered configuration used only to prepare and read required data."""

    async def read_async(self) -> dict[str, list[Seed[Any]]]:
        """
        Read every required dataset in full and run the validators.

        Returns:
            dict[str, list[Seed]]: Seeds keyed by dataset name, in declaration order.
        """
        seeds_by_dataset = await self._collect_named_seeds_async()
        self.validate(
            ResolvedDataset(
                seeds=[seed for seeds in seeds_by_dataset.values() for seed in seeds],
                source_kind=self.source_kind,
                dataset_names=tuple(seeds_by_dataset),
            )
        )
        return seeds_by_dataset


class ScenarioRequiredData:
    """
    The datasets a scenario reads in full to build its attack population.

    Each source keeps its provider and fetch policy, so preparation and refresh behave
    exactly like a ``dataset_config`` source. Required datasets can't be capped, sampled,
    filtered, or deselected; limit the assembled attack groups through the scenario's
    ``dataset_config`` instead.
    """

    def __init__(
        self,
        *,
        sources: Sequence[DatasetSource],
        fetch: DatasetFetchPolicy = DatasetFetchPolicy.IF_MISSING,
        validators: Sequence[Callable[[ResolvedDataset], None]] | None = None,
    ) -> None:
        """
        Declare the required datasets.

        Args:
            sources (Sequence[DatasetSource]): The required datasets, with optional providers and fetch policies.
            fetch (DatasetFetchPolicy): The default preparation policy for sources without their own.
            validators (Sequence[Callable[[ResolvedDataset], None]] | None): Checks run on the full
                required data, both on normal reads and on refresh candidates before they're stored.

        Raises:
            DatasetConstraintError: If no source is given or a source sets a size limit.
        """
        if not sources:
            raise DatasetConstraintError("Required data needs at least one dataset source.")
        capped = [source.name for source in sources if source.max_size not in ("default", "all")]
        if capped:
            raise DatasetConstraintError(f"Required datasets are read in full and can't be capped: {capped}.")
        self._validators = list(validators or [])
        self._configuration = _RequiredDatasetConfiguration(
            sources=list(sources),
            max_per_dataset="all",
            max_total="all",
            fetch=fetch,
            validators=self._validators,
        )

    @property
    def sources(self) -> tuple[DatasetSource, ...]:
        """The required dataset sources."""
        return self._configuration.sources

    @property
    def dataset_names(self) -> list[str]:
        """The required dataset names, in declaration order."""
        return self._configuration.dataset_names

    @property
    def fetch(self) -> DatasetFetchPolicy:
        """The default preparation policy."""
        return self._configuration.fetch

    async def prepare_async(self, *, fetch: DatasetFetchPolicy | None = None) -> None:
        """
        Load or refresh the required datasets, like ``DatasetConfiguration.prepare_async``.

        Args:
            fetch (DatasetFetchPolicy | None): Optional policy for this operation only.
        """
        await self._configuration.prepare_async(fetch=fetch)

    async def load_async(self) -> dict[str, list[Seed[Any]]]:
        """
        Read the required datasets from memory without fetching.

        Returns:
            dict[str, list[Seed]]: Seeds keyed by dataset name, in declaration order.
        """
        return await self._configuration.read_async()

    def apply_overrides(
        self,
        *,
        request: ScenarioDatasetRequest,
        options: ScenarioDatasetOptions,
        population_names: Sequence[str],
    ) -> tuple[ScenarioRequiredData, ScenarioDatasetRequest]:
        """
        Take the fetch edits that belong to required data out of a dataset request.

        Args:
            request (ScenarioDatasetRequest): The requested dataset edits.
            options (ScenarioDatasetOptions): The scenario's dataset capabilities.
            population_names (Sequence[str]): The population source names before the edits.

        Returns:
            tuple[ScenarioRequiredData, ScenarioDatasetRequest]: The edited required data, and the
            request left for the population configuration.

        Raises:
            DatasetConstraintError: If the request deselects, caps, or reorders required datasets.
        """
        if not options.supports_overrides and request != ScenarioDatasetRequest():
            raise DatasetConstraintError("This scenario does not support dataset overrides.")
        if request.sources is None:
            return self._with_fetch(fetch=request.fetch, source_fetch={}), request
        names = self.dataset_names
        requested = [source.name for source in request.sources]
        fixed = [*names, *population_names]
        if options.mode is DatasetSelectionMode.FIXED and requested != fixed:
            raise DatasetConstraintError(f"Dataset sources are fixed: {', '.join(fixed)}.")
        missing = [name for name in names if name not in requested]
        if missing:
            raise DatasetConstraintError(f"Required datasets can't be deselected: {', '.join(missing)}.")
        required_inputs = [source for source in request.sources if source.name in names]
        capped = [source.name for source in required_inputs if source.max_size != "default"]
        if capped:
            raise DatasetConstraintError(f"Required datasets are read in full and can't be capped: {capped}.")
        remaining = [source for source in request.sources if source.name not in names]
        edited = self._with_fetch(
            fetch=request.fetch,
            source_fetch={source.name: source.fetch for source in required_inputs if source.fetch is not None},
        )
        return edited, request.model_copy(update={"sources": remaining or None})

    def project_sources(self) -> list[ScenarioDatasetSourceSettings]:
        """
        Describe the required sources for dataset settings responses, without reading memory.

        Returns:
            list[ScenarioDatasetSourceSettings]: One uncapped entry per required dataset.
        """
        return [
            ScenarioDatasetSourceSettings(
                name=source.name,
                max_size="all",
                fetch=source.fetch or self.fetch,
                configured_provider=source.provider is not None,
            )
            for source in self.sources
        ]

    def with_sources(self, *, sources: Sequence[DatasetSource], fetch: DatasetFetchPolicy) -> ScenarioRequiredData:
        """
        Copy the declaration with replacement sources, keeping its validators.

        Args:
            sources (Sequence[DatasetSource]): The replacement sources.
            fetch (DatasetFetchPolicy): The default preparation policy.

        Returns:
            ScenarioRequiredData: The new declaration.
        """
        return ScenarioRequiredData(sources=sources, fetch=fetch, validators=self._validators)

    def _with_fetch(
        self, *, fetch: DatasetFetchPolicy | None, source_fetch: dict[str, DatasetFetchPolicy]
    ) -> ScenarioRequiredData:
        if fetch is None and not source_fetch:
            return self
        sources = [
            replace(source, fetch=source_fetch[source.name]) if source.name in source_fetch else source
            for source in self.sources
        ]
        return self.with_sources(sources=sources, fetch=fetch or self.fetch)
