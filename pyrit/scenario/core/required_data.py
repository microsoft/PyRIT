# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Datasets a scenario needs in order to build its attacks.

Some scenarios assemble their attack population from datasets used as ingredients
(templates, registries, partial keys). Those datasets are not the population a user
selects or limits: every row that remains after filtering is read, without source caps or
sampling. Filters narrow the input rows; they don't limit the assembled attacks.
``ScenarioRequiredData`` declares them apart from ``dataset_config``, which then only describes
and limits the assembled attack groups.

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
    prepare_configurations_async,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from pyrit.models import Seed


class _RequiredDatasetConfiguration(DatasetConfiguration):
    """An uncapped, unsampled configuration used only to prepare and read required data."""

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
    exactly like a ``dataset_config`` source. Dataset filters narrow the rows that are read,
    as they do for any source. Required datasets can't be capped, sampled, or deselected;
    limit the assembled attack groups through the scenario's ``dataset_config`` instead.
    """

    def __init__(
        self,
        *,
        sources: Sequence[DatasetSource],
        fetch: DatasetFetchPolicy = DatasetFetchPolicy.IF_MISSING,
        validators: Sequence[Callable[[ResolvedDataset], None]] | None = None,
        filters: dict[str, list[str]] | None = None,
    ) -> None:
        """
        Declare the required datasets.

        Args:
            sources (Sequence[DatasetSource]): The required datasets, with optional providers and fetch policies.
            fetch (DatasetFetchPolicy): The default preparation policy for sources without their own.
            validators (Sequence[Callable[[ResolvedDataset], None]] | None): Checks run on the full
                required data, both on normal reads and on refresh candidates before they're stored.
            filters (dict[str, list[str]] | None): ``get_seeds`` filters applied when reading, before
                the validators run.

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
            filters=filters,
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

    @property
    def filters(self) -> dict[str, list[str]]:
        """The ``get_seeds`` filters applied when reading."""
        return self._configuration.filters

    async def prepare_async(
        self,
        *,
        fetch: DatasetFetchPolicy | None = None,
        population: DatasetConfiguration | None = None,
        validate_assembly: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        """
        Load or refresh the required datasets, like ``DatasetConfiguration.prepare_async``.

        With a population configuration, both are fetched first and validated together before
        anything is written: a refresh of either half is checked against the other half's
        candidates, so a rejected refresh leaves every stored dataset unchanged.

        Args:
            fetch (DatasetFetchPolicy | None): Optional policy for this operation only.
            population (DatasetConfiguration | None): The scenario's population configuration to prepare
                in the same batch.
            validate_assembly (Callable[[], Awaitable[object]] | None): The scenario's full unsampled
                assembly, run against the candidates whenever a dataset is refreshed.
        """
        configurations = [self._configuration, *([population] if population is not None else [])]
        await prepare_configurations_async(
            configurations=configurations, fetch=fetch, validate_assembly=validate_assembly
        )

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
        Take the fetch edits for required sources out of a dataset request.

        The request's global fetch policy reaches required sources without their own policy and
        also stays on the returned request for the population, as do its filters.

        Args:
            request (ScenarioDatasetRequest): The requested dataset edits.
            options (ScenarioDatasetOptions): The scenario's dataset capabilities.
            population_names (Sequence[str]): The population source names before the edits.

        Returns:
            tuple[ScenarioRequiredData, ScenarioDatasetRequest]: The edited required data, and the
            request left for the population configuration.

        Raises:
            DatasetConstraintError: If the request deselects, caps, or reorders required datasets, or
                leaves out every population dataset.
        """
        if not options.supports_overrides and request != ScenarioDatasetRequest():
            raise DatasetConstraintError("This scenario does not support dataset overrides.")
        required = self if request.fetch is None else self.with_sources(sources=self.sources, fetch=request.fetch)
        if request.sources is None:
            return required, request
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
        # A request can't hold an empty source list, and dropping it would silently restore the defaults.
        if not remaining and population_names:
            raise DatasetConstraintError(
                "Select at least one population dataset besides the required ones "
                f"(defaults: {', '.join(population_names)})."
            )
        edited = required._with_source_fetch(
            {source.name: source.fetch for source in required_inputs if source.fetch is not None}
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

    def with_sources(
        self,
        *,
        sources: Sequence[DatasetSource],
        fetch: DatasetFetchPolicy,
        filters: dict[str, list[str]] | None = None,
        validators: Sequence[Callable[[ResolvedDataset], None]] | None = None,
    ) -> ScenarioRequiredData:
        """
        Copy the declaration with replacement sources, keeping its validators.

        Args:
            sources (Sequence[DatasetSource]): The replacement sources.
            fetch (DatasetFetchPolicy): The default preparation policy.
            filters (dict[str, list[str]] | None): Filters merged over the current ones, as overrides do.
            validators (Sequence[Callable[[ResolvedDataset], None]] | None): Validators appended to the
                kept ones, used when a legacy ``dataset_config`` migrates its validators onto the required data.

        Returns:
            ScenarioRequiredData: The new declaration.
        """
        merged = {**self.filters, **{key: list(values) for key, values in (filters or {}).items()}}
        return ScenarioRequiredData(
            sources=sources,
            fetch=fetch,
            validators=[*self._validators, *(validators or [])],
            filters=merged,
        )

    def _with_source_fetch(self, source_fetch: dict[str, DatasetFetchPolicy]) -> ScenarioRequiredData:
        if not source_fetch:
            return self
        sources = [
            replace(source, fetch=source_fetch[source.name]) if source.name in source_fetch else source
            for source in self.sources
        ]
        return self.with_sources(sources=sources, fetch=self.fetch)
