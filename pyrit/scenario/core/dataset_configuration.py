# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset configuration for scenarios.

``DatasetConfiguration`` is the object a scenario uses to say "where do my seeds come
from." ``DatasetAttackConfiguration`` -- the configuration most scenarios use -- groups
the resolved seeds into ``AttackSeedGroup`` s (each carrying exactly one objective plus
optional prompts).

Constraints are expressed through a single mechanism: ``validators``. Each validator is a
``Callable[[ResolvedDataset], None]`` that raises ``DatasetConstraintError`` on violation.
Validators run against the fully resolved dataset (before source or total sampling),
so they describe the dataset itself, not the sampled subset. The ``ResolvedDataset`` they
receive also carries the ``DatasetSourceKind`` (inline vs from memory) and the contributing
``dataset_names``, which lets a scenario require or forbid inline seeds -- useful for CLI
flags such as ``--objectives`` -- restrict which datasets it will resolve from, or require a
particular seed type (e.g. ``require_seed_type(SeedObjective)``).

Memory is the source of truth. ``prepare_async`` checks all sources before fetching
missing registered datasets. Read methods never fetch or write. Selection applies each
source's limit before the combined ``max_total`` limit. Inline configurations never
touch memory.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING, Any, Literal, Self, TypeVar, cast

from pyrit.common.deprecation import print_deprecation_message
from pyrit.memory import CentralMemory
from pyrit.models import (
    AllAvailableDatasetSize,
    AttackSeedGroup,
    BoundedDatasetSize,
    IndeterminateDatasetSize,
    ScenarioDatasetSizeEstimate,
    Seed,
    SeedGroup,
    group_seeds_into_attack_groups,
    scenario_dataset_size_from_limit,
)
from pyrit.models.dataset_limit import DatasetLimit, ResolvedDatasetLimit, normalize_dataset_limit

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pyrit.memory import MemoryInterface

# Dataset-name label that inline ``seeds`` / ``seed_groups`` carry in by-dataset views, since
# they have no real dataset name. Inline and named sources are mutually exclusive, so this
# never collides with a configured dataset name.
INLINE_DATASET_NAME = "inline"

# Internal helper TypeVar for size-capping any homogeneous list.
_ItemT = TypeVar("_ItemT")


class _Unset(Enum):
    VALUE = "unset"


class DatasetFetchPolicy(str, Enum):
    """When preparation may load a registered dataset into memory."""

    NEVER = "never"
    IF_MISSING = "if_missing"


def _resolve_limit(*, name: str, value: DatasetLimit | _Unset, default: ResolvedDatasetLimit) -> ResolvedDatasetLimit:
    try:
        limit = normalize_dataset_limit(None if isinstance(value, _Unset) else value)
    except ValueError as exc:
        raise DatasetConstraintError(f"'{name}': {exc}") from exc
    return default if limit == "default" else limit


def _deprecated_argument(*, old: str, new: str) -> None:
    print_deprecation_message(
        old_item=f"DatasetConfiguration({old})",
        new_item=f"DatasetConfiguration({new})",
        removed_in="1.4.0",
    )


@dataclass(frozen=True, kw_only=True)
class DatasetSource:
    """A named dataset, with optional overrides for its selection limit and fetch policy."""

    name: str
    max_size: DatasetLimit = "default"
    fetch: DatasetFetchPolicy | None = None

    def __post_init__(self) -> None:
        """
        Validate source options without reading memory or providers.

        Raises:
            DatasetConstraintError: If the name, limit, or policy is invalid.
        """
        if not isinstance(self.name, str) or not self.name.strip():
            raise DatasetConstraintError("Dataset source names must be non-empty strings.")
        try:
            object.__setattr__(self, "max_size", normalize_dataset_limit(self.max_size))
        except ValueError as exc:
            raise DatasetConstraintError(f"'max_size': {exc}") from exc
        if self.fetch is not None and not isinstance(self.fetch, DatasetFetchPolicy):
            raise DatasetConstraintError("'fetch' must be a DatasetFetchPolicy.")


class DatasetSourceKind(Enum):
    """
    How a ``DatasetConfiguration``'s seeds were sourced.

    Only two cases matter to validators: seeds supplied inline by the caller, versus
    seeds loaded from memory by dataset name (prepared explicitly before reading).
    This lets a constraint require or forbid inline data -- e.g. a CLI
    ``--objectives`` flag that must be passed inline rather than via a named dataset.
    """

    INLINE = "inline"
    MEMORY = "memory"


@dataclass(frozen=True)
class ResolvedDataset:
    """
    The fully resolved seeds plus the source they came from.

    Passed to every validator so a constraint can inspect the seeds, how they were
    supplied (inline vs named dataset), and which dataset names contributed.

    Args:
        seeds (Sequence[Seed]): The resolved seeds before sampling.
        source_kind (DatasetSourceKind): How the configuration was sourced.
        dataset_names (tuple[str, ...]): The configured dataset names that contributed
            seeds, in configuration order. Empty for inline ``seeds`` / ``seed_groups``.
    """

    seeds: Sequence[Seed]
    source_kind: DatasetSourceKind
    dataset_names: tuple[str, ...] = ()

    @property
    def is_inline(self) -> bool:
        """
        Whether the seeds were supplied inline (not loaded from a named dataset).

        Returns:
            bool: True for inline ``seeds=`` / ``seed_groups=`` sources.
        """
        return self.source_kind is DatasetSourceKind.INLINE


class DatasetConstraintError(ValueError):
    """
    Raised when a resolved dataset does not satisfy a configuration's constraints.

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers keep working,
    while letting the CLI/backend present a friendly "dataset X doesn't satisfy
    scenario Y's requirements" message.
    """


def require_nonempty() -> Callable[[ResolvedDataset], None]:
    """
    Build a validator that raises when a resolved dataset is empty.

    Returns:
        Callable[[ResolvedDataset], None]: A validator usable in ``validators=[...]``.
    """

    def _validate(resolved: ResolvedDataset) -> None:
        if not resolved.seeds:
            raise DatasetConstraintError("Resolved dataset is empty.")

    return _validate


def require_min_size(minimum: int) -> Callable[[ResolvedDataset], None]:
    """
    Build a validator that raises when a resolved dataset has fewer than ``minimum`` items.

    Args:
        minimum (int): The minimum acceptable number of items.

    Returns:
        Callable[[ResolvedDataset], None]: A validator usable in ``validators=[...]``.
    """

    def _validate(resolved: ResolvedDataset) -> None:
        if len(resolved.seeds) < minimum:
            raise DatasetConstraintError(
                f"Resolved dataset has {len(resolved.seeds)} item(s); require at least {minimum}."
            )

    return _validate


def require_harm_categories(required: set[str]) -> Callable[[ResolvedDataset], None]:
    """
    Build a validator that requires every resolved item to carry all of ``required`` harm categories.

    Args:
        required (set[str]): Harm categories every item must include.

    Returns:
        Callable[[ResolvedDataset], None]: A validator usable in ``validators=[...]``.
    """

    def _validate(resolved: ResolvedDataset) -> None:
        for item in resolved.seeds:
            categories = set(getattr(item, "harm_categories", None) or [])
            missing = required - categories
            if missing:
                raise DatasetConstraintError(f"Resolved item is missing required harm categories: {sorted(missing)}.")

    return _validate


def require_seed_type(seed_type: type[Seed]) -> Callable[[ResolvedDataset], None]:
    """
    Build a validator that requires every resolved seed to be an instance of ``seed_type``.

    Args:
        seed_type (type[Seed]): The seed type every resolved seed must be.

    Returns:
        Callable[[ResolvedDataset], None]: A validator usable in ``validators=[...]``.
    """

    def _validate(resolved: ResolvedDataset) -> None:
        wrong = {type(seed).__name__ for seed in resolved.seeds if not isinstance(seed, seed_type)}
        if wrong:
            raise DatasetConstraintError(f"Expected all seeds to be {seed_type.__name__}; found {sorted(wrong)}.")

    return _validate


def require_inline_seeds() -> Callable[[ResolvedDataset], None]:
    """
    Build a validator that requires the dataset to be supplied inline.

    Use when a scenario must receive seeds directly (e.g. CLI ``--objectives``) rather
    than via a named dataset.

    Returns:
        Callable[[ResolvedDataset], None]: A validator usable in ``validators=[...]``.
    """

    def _validate(resolved: ResolvedDataset) -> None:
        if not resolved.is_inline:
            raise DatasetConstraintError(
                "This configuration requires inline seeds (pass 'seeds' or 'seed_groups'), not a named dataset."
            )

    return _validate


def forbid_inline_seeds() -> Callable[[ResolvedDataset], None]:
    """
    Build a validator that forbids inline seeds (the dataset must come from named datasets).

    Use when a scenario must resolve from memory/providers and inline seeds would bypass
    expected curation.

    Returns:
        Callable[[ResolvedDataset], None]: A validator usable in ``validators=[...]``.
    """

    def _validate(resolved: ResolvedDataset) -> None:
        if resolved.is_inline:
            raise DatasetConstraintError("This configuration does not allow inline seeds; use 'dataset_names' instead.")

    return _validate


def restrict_dataset_names(allowed: set[str]) -> Callable[[ResolvedDataset], None]:
    """
    Build a validator that requires every contributing dataset name to be in ``allowed``.

    Use when a scenario only knows how to handle a fixed set of datasets -- for example,
    one that pairs techniques with specific datasets -- so a caller-supplied
    ``--dataset-names`` outside that set is rejected loudly. Inline seeds carry no dataset
    name and therefore pass; compose with ``forbid_inline_seeds`` to also require named
    datasets.

    Args:
        allowed (set[str]): The dataset names the configuration may resolve from.

    Returns:
        Callable[[ResolvedDataset], None]: A validator usable in ``validators=[...]``.
    """

    def _validate(resolved: ResolvedDataset) -> None:
        disallowed = sorted(set(resolved.dataset_names) - allowed)
        if disallowed:
            raise DatasetConstraintError(
                f"Datasets {disallowed} are not allowed for this configuration; "
                f"permitted datasets are {sorted(allowed)}."
            )

    return _validate


class DatasetConfiguration:
    """
    Configuration describing where a scenario's seeds come from.

    This base class separates preparation from read-only resolution and validation.
    ``DatasetAttackConfiguration`` is the concrete subclass most scenarios use; it groups
    the resolved seeds into ``AttackSeedGroup`` s. A configuration draws from exactly one
    source:

    - ``seeds`` -- an explicit, inline list of seeds (never touches memory).
    - ``seed_groups`` -- explicit, inline seed groups (never touches memory).
    - ``sources`` -- named datasets read from memory after explicit preparation.

    Constraints are expressed through a single mechanism -- ``validators`` -- so there is
    one place to look. Customize behavior through small seams without re-implementing
    sampling/fetching:

    - ``_default_validators`` -- validators a subclass always applies (e.g. a seed-type
      check). The preferred way to enforce a constraint type-wide.
    - ``_collect_seeds_for_dataset_async`` -- the per-dataset memory query (override for
      richer filters).
    """

    def __init__(
        self,
        *,
        seeds: Sequence[Seed] | None = None,
        seed_groups: list[SeedGroup] | None = None,
        sources: Sequence[DatasetSource] | None = None,
        max_per_dataset: DatasetLimit = "default",
        max_total: DatasetLimit | _Unset = _Unset.VALUE,
        fetch: DatasetFetchPolicy | _Unset = _Unset.VALUE,
        dataset_names: list[str] | None = None,
        max_dataset_size: DatasetLimit | _Unset = _Unset.VALUE,
        filters: dict[str, list[str]] | None = None,
        validators: Sequence[Callable[[ResolvedDataset], None]] | None = None,
        auto_fetch: bool | _Unset = _Unset.VALUE,
    ) -> None:
        """
        Initialize a DatasetConfiguration.

        Args:
            seeds (Sequence[Seed] | None): Explicit, inline seeds (never touches memory).
            seed_groups (list[SeedGroup] | None): Explicit, inline seed groups (never
                touches memory).
            sources (Sequence[DatasetSource] | None): Named datasets to prepare and read.
            max_per_dataset (DatasetLimit): Source cap. None, empty, and "default" use the default;
                "all" removes the cap.
            max_total (DatasetLimit | _Unset): Combined cap, with the same default/all semantics.
            fetch (DatasetFetchPolicy | _Unset): Default preparation policy; defaults to IF_MISSING.
            dataset_names (list[str] | None): Deprecated alias for sources.
            max_dataset_size (DatasetLimit | _Unset): Deprecated alias for max_total.
            filters (dict[str, list[str]] | None): Filters passed to ``MemoryInterface.get_seeds``
                when resolving named datasets (e.g. ``{"harm_categories": ["cyber"]}``).
                Applied before sampling; ignored for inline seeds.
            validators (Sequence[Callable[[ResolvedDataset], None]] | None): Constraint
                callbacks run against the resolved dataset; each raises on violation. These
                are appended to the subclass's ``_default_validators``.
            auto_fetch (bool | _Unset): Deprecated preparation-policy alias.

        Raises:
            ValueError: If source definitions or aliases conflict, or selection options are invalid.
        """
        if sum(src is not None for src in (seeds, seed_groups, sources, dataset_names)) > 1:
            raise ValueError("Only one of 'seeds', 'seed_groups', 'sources', or 'dataset_names' can be set.")
        if dataset_names is not None:
            _deprecated_argument(old="dataset_names=...", new="sources=...")
            sources = [DatasetSource(name=name) for name in dataset_names]
        if not isinstance(max_dataset_size, _Unset):
            if not isinstance(max_total, _Unset):
                raise ValueError("Use only one of 'max_dataset_size' and 'max_total'.")
            _deprecated_argument(old="max_dataset_size=...", new="max_total=...")
            max_total = max_dataset_size
        if not isinstance(auto_fetch, _Unset):
            if not isinstance(fetch, _Unset):
                raise ValueError("Use only one of 'auto_fetch' and 'fetch'.")
            if type(auto_fetch) is not bool:
                raise ValueError("'auto_fetch' must be a bool.")
            _deprecated_argument(old="auto_fetch=...", new="fetch=...")
            fetch = DatasetFetchPolicy.IF_MISSING if auto_fetch else DatasetFetchPolicy.NEVER
        self._seeds = list(seeds) if seeds is not None else None
        self._seed_groups = list(seed_groups) if seed_groups is not None else None
        self.sources = tuple(sources or ())
        self.max_per_dataset = max_per_dataset
        self.max_total = None if isinstance(max_total, _Unset) else max_total
        self.fetch = DatasetFetchPolicy.IF_MISSING if isinstance(fetch, _Unset) else fetch
        self._filters: dict[str, list[str]] = dict(filters or {})
        self._validators: list[Callable[[ResolvedDataset], None]] = [
            *self._default_validators(),
            *(list(validators) if validators else []),
        ]
        self._validate_selection_options()

    def _validate_selection_options(self) -> None:
        if not isinstance(self.fetch, DatasetFetchPolicy):
            raise DatasetConstraintError("'fetch' must be a DatasetFetchPolicy.")
        if (self._seeds is not None or self._seed_groups is not None) and self.max_per_dataset != "all":
            raise DatasetConstraintError("Inline seeds do not support per-dataset limits; use max_total.")
        names = [source.name for source in self.sources]
        if len(names) != len(set(names)):
            raise DatasetConstraintError("Duplicate dataset source names are not allowed.")

    @property
    def max_total(self) -> ResolvedDatasetLimit:
        """The resolved combined cap, or "all" for no total cap."""
        return self._max_total

    @max_total.setter
    def max_total(self, value: DatasetLimit) -> None:
        self._max_total = _resolve_limit(name="max_total", value=value, default=self._default_max_total())

    @property
    def max_per_dataset(self) -> ResolvedDatasetLimit:
        """The resolved inherited source cap, or "all" for no source cap."""
        return self._max_per_dataset

    @max_per_dataset.setter
    def max_per_dataset(self, value: DatasetLimit) -> None:
        self._max_per_dataset = _resolve_limit(
            name="max_per_dataset", value=value, default=self._default_max_per_dataset()
        )

    def _default_max_total(self) -> ResolvedDatasetLimit:
        return "all"

    def _default_max_per_dataset(self) -> ResolvedDatasetLimit:
        return "all"

    @property
    def max_dataset_size(self) -> ResolvedDatasetLimit:
        """The deprecated alias for the total selection limit."""
        _deprecated_argument(old="max_dataset_size", new="max_total")
        return self.max_total

    @max_dataset_size.setter
    def max_dataset_size(self, value: DatasetLimit) -> None:
        _deprecated_argument(old="max_dataset_size", new="max_total")
        self.max_total = value

    def with_overrides(
        self,
        *,
        sources: Sequence[DatasetSource] | _Unset = _Unset.VALUE,
        max_per_dataset: DatasetLimit | _Unset = _Unset.VALUE,
        max_total: DatasetLimit | _Unset = _Unset.VALUE,
        fetch: DatasetFetchPolicy | _Unset = _Unset.VALUE,
        filters: dict[str, list[str]] | None = None,
    ) -> Self:
        """
        Copy this configuration, preserving its concrete class and custom state.

        Default-valued limit overrides keep the current settings. "all" removes only that cap.

        Returns:
            Self: An independent selection configuration with shared live objects.

        Raises:
            DatasetConstraintError: If named sources replace inline seeds or options are invalid.
        """
        result = copy.copy(self)
        result._filters = {key: list(values) for key, values in self._filters.items()}
        result._validators = list(self._validators)
        if not isinstance(sources, _Unset):
            if self.source_kind is DatasetSourceKind.INLINE:
                raise DatasetConstraintError("Cannot replace inline seeds with named sources through overrides.")
            result.sources = tuple(sources)
        result.max_per_dataset = _resolve_limit(
            name="max_per_dataset", value=max_per_dataset, default=self.max_per_dataset
        )
        result.max_total = _resolve_limit(name="max_total", value=max_total, default=self.max_total)
        if not isinstance(fetch, _Unset):
            result.fetch = fetch
        if filters is not None:
            result.update_filters(filters=filters)
        result._validate_selection_options()
        return result

    def source_limit(self, name: str) -> ResolvedDatasetLimit:
        """Return the effective limit for a configured objective source."""
        source = next(source for source in self.sources if source.name == name)
        return _resolve_limit(name="max_size", value=source.max_size, default=self.max_per_dataset)

    @property
    def has_sampling_limits(self) -> bool:
        """Whether this configuration can select a subset of its groups."""
        return self.max_total != "all" or any(self.source_limit(source.name) != "all" for source in self.sources)

    def _preparation_sources(self) -> list[tuple[str, DatasetFetchPolicy]]:
        return [(source.name, source.fetch or self.fetch) for source in self.sources]

    async def prepare_async(self) -> None:
        """
        Check all sources, then populate missing registered datasets in memory.

        Raises:
            DatasetConstraintError: If a missing source cannot be fetched or provider data is invalid.
        """
        self.validate_configuration()
        if self.source_kind is DatasetSourceKind.INLINE:
            return
        policies: dict[str, DatasetFetchPolicy] = {}
        for name, policy in self._preparation_sources():
            if name in policies and policies[name] is not policy:
                raise DatasetConstraintError(f"Dataset '{name}' has conflicting fetch policies.")
            policies[name] = policy
        if not policies:
            return
        stored_names = set(await self._memory.get_seed_dataset_names_async())
        missing: list[str] = []
        for name, policy in policies.items():
            if name in stored_names:
                continue
            if policy is DatasetFetchPolicy.NEVER:
                raise DatasetConstraintError(f"Dataset '{name}' is missing and fetch is 'never'. Import it first.")
            missing.append(name)
        if not missing:
            return
        from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider

        providers = await SeedDatasetProvider.get_providers_by_name_async(dataset_names=missing)
        unavailable = [name for name in missing if name not in providers]
        if unavailable:
            raise DatasetConstraintError(f"Datasets {unavailable} are missing and have no provider. Import them first.")
        for name in missing:
            dataset = await providers[name].fetch_dataset_async()
            if (
                not dataset.seeds
                or dataset.dataset_name != name
                or any(seed.dataset_name != name for seed in dataset.seeds)
            ):
                raise DatasetConstraintError(
                    f"Provider for '{name}' must return non-empty seeds with that dataset name."
                )
            await self._memory.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="DatasetConfiguration")

    def _default_validators(self) -> list[Callable[[ResolvedDataset], None]]:
        """
        Return validators a subclass always applies, prepended to user-supplied ``validators``.

        The base requires a non-empty resolved dataset. A subclass can extend this to enforce
        an additional constraint (e.g. ``require_seed_type(SeedObjective)``) by returning
        ``[*super()._default_validators(), ...]`` rather than overriding ``validate``.

        Returns:
            list[Callable[[ResolvedDataset], None]]: The default validators.
        """
        return [require_nonempty()]

    @cached_property
    def _memory(self) -> MemoryInterface:
        """
        The central memory instance, resolved lazily on first use and cached.

        Resolved lazily (rather than in ``__init__``) so a configuration can be
        constructed for introspection -- e.g. the scenario registry instantiating a
        scenario to read its default dataset names -- without a memory instance set.

        Returns:
            MemoryInterface: The central memory instance.
        """
        return CentralMemory.get_memory_instance()

    @property
    def dataset_names(self) -> list[str]:
        """
        The configured dataset names.

        Returns:
            list[str]: The dataset names, or an empty list when using inline seeds/groups.
        """
        return [source.name for source in self.sources]

    @property
    def source_kind(self) -> DatasetSourceKind:
        """
        Whether this configuration's seeds are supplied inline or loaded from memory.

        Inline ``seeds`` / ``seed_groups`` resolve to ``INLINE``; named datasets (and an
        unconfigured source) resolve to ``MEMORY``.

        Returns:
            DatasetSourceKind: The source kind.
        """
        if self._seeds is not None or self._seed_groups is not None:
            return DatasetSourceKind.INLINE
        return DatasetSourceKind.MEMORY

    @property
    def filters(self) -> dict[str, list[str]]:
        """
        The ``get_seeds`` filters applied when resolving named datasets.

        Returns:
            dict[str, list[str]]: A copy of the configured filters.
        """
        return dict(self._filters)

    def get_size_budget(self) -> ScenarioDatasetSizeEstimate:
        """Return the configured selection budget without reading or sampling seeds."""
        if not self.sources:
            return scenario_dataset_size_from_limit(self.max_total)
        limits = [self.source_limit(source.name) for source in self.sources]
        if any(limit == "all" for limit in limits):
            return scenario_dataset_size_from_limit(self.max_total)
        total = sum(limit for limit in limits if isinstance(limit, int))
        return BoundedDatasetSize(value=min(total, self.max_total) if self.max_total != "all" else total)

    def validate_configuration(self) -> None:
        """
        Check parameter constraints without resolving dataset contents.

        Raises:
            DatasetConstraintError: If the selection cap is not positive.
        """
        self._validate_selection_options()

    def size_caps_by_dataset(self) -> dict[str, list[tuple[str, int, Literal["dataset", "configuration", "compound"]]]]:
        """
        Describe configured caps for each named dataset or inline source.

        Returns:
            dict[str, list[tuple[str, int, Literal]]]: Source name to ordered
            ``(cap label, count, provenance)`` entries.
        """
        caps: dict[str, list[tuple[str, int, Literal["dataset", "configuration", "compound"]]]] = {}
        for name in self.dataset_names or [INLINE_DATASET_NAME]:
            entries: list[tuple[str, int, Literal["dataset", "configuration", "compound"]]] = []
            limit = self.source_limit(name) if self.sources else "all"
            if limit != "all":
                entries.append(("per-dataset cap", limit, "dataset"))
            if self.max_total != "all":
                entries.append(("combined configuration cap", self.max_total, "configuration"))
            caps[name] = entries
        return caps

    @property
    def _get_seeds_filters(self) -> dict[str, Any]:
        """
        The configured filters widened to ``Any`` for ``get_seeds`` keyword unpacking.

        ``get_seeds`` has a heterogeneous signature, so the list-valued filters must be widened
        at this boundary before being unpacked as ``**kwargs``.

        Returns:
            dict[str, Any]: The filters typed for keyword unpacking.
        """
        return cast("dict[str, Any]", self._filters)

    def update_filters(self, *, filters: dict[str, list[str]]) -> None:
        """
        Merge additional ``get_seeds`` filters into this configuration (run-time override).

        Used when a run overrides dataset selection without rebuilding the configuration --
        the provided filters take precedence over any already configured with the same key.

        Args:
            filters (dict[str, list[str]]): Filters to merge, keyed by ``get_seeds`` kwarg name.
        """
        self._filters = {**self._filters, **{key: list(values) for key, values in filters.items()}}

    # =========================================================================
    # Resolution helpers
    # =========================================================================

    async def _collect_named_seeds_async(self) -> dict[str, list[Seed]]:
        """
        Collect seeds for each configured dataset name, keyed by name.

        Each name is read from memory. A missing dataset requires explicit preparation
        or import; a read never calls a provider.

        Returns:
            dict[str, list[Seed]]: Dataset name -> seeds, in configuration order (every value
                is non-empty).

        Raises:
            DatasetConstraintError: If any configured dataset yields no seeds.
        """
        result: dict[str, list[Seed]] = {}
        for name in self.dataset_names:
            result[name] = await self._collect_seeds_for_dataset_async(dataset_name=name)
        return result

    async def _collect_seeds_for_dataset_async(self, *, dataset_name: str) -> list[Seed]:
        """
        Read seeds for a single dataset name without fetching or writing.

        Args:
            dataset_name (str): The dataset name to load.

        Returns:
            list[Seed]: The seeds for ``dataset_name``.

        Raises:
            DatasetConstraintError: If the dataset is absent or its filters match no seeds.
        """
        found = list(await self._memory.get_seeds_async(dataset_name=dataset_name, **self._get_seeds_filters))
        if not found:
            unfiltered = await self._memory.get_seeds_async(dataset_name=dataset_name) if self._filters else []
            if unfiltered:
                raise DatasetConstraintError(
                    f"Dataset '{dataset_name}' has seeds, but none match the configured filters {self._filters}."
                )
            raise DatasetConstraintError(
                f"Dataset '{dataset_name}' could not be loaded: no seeds found in memory. "
                "Call prepare_async() before reading a new selection, or import the dataset."
            )
        return found

    def validate(self, resolved: ResolvedDataset) -> None:
        """
        Validate the resolved dataset against every configured validator.

        Runs the defaults from ``_default_validators`` (non-emptiness, plus any seed-type
        constraint a subclass imposes) followed by any validators passed to ``validators=``.
        Prefer adding a validator over overriding this method.

        Args:
            resolved (ResolvedDataset): The resolved seeds and their source kind.

        Raises:
            DatasetConstraintError: If any constraint is violated.
        """
        for validator in self._validators:
            validator(resolved)

    def _apply_max_dataset_size(self, items: list[_ItemT]) -> list[_ItemT]:
        """
        Apply ``max_total`` sampling without replacement.

        Args:
            items (list[_ItemT]): The items to potentially sample from.

        Returns:
            list[_ItemT]: The original list, or a random sample of up to
                ``max_total`` unique items.
        """
        if self.max_total == "all" or len(items) <= self.max_total:
            return items
        return random.sample(items, self.max_total)


@dataclass(frozen=True)
class _ResolvedAttackGroups:
    """A per-read snapshot that validates compound populations before any sampling."""

    configuration: DatasetAttackConfiguration
    groups: dict[str, list[AttackSeedGroup]]
    children: tuple[_ResolvedAttackGroups, ...] = ()

    def select(self, *, apply_sampling: bool) -> dict[str, list[AttackSeedGroup]]:
        if not apply_sampling:
            return self.groups
        groups = self.groups
        if self.children:
            groups = {}
            for child in self.children:
                for name, population in child.select(apply_sampling=True).items():
                    groups.setdefault(name, []).extend(population)
        return self.configuration._sample_groups_by_dataset(groups)


class DatasetAttackConfiguration(DatasetConfiguration):
    """
    A ``DatasetConfiguration`` that groups resolved seeds into attack groups.

    This is the default most scenarios use: scenarios run over ``AttackSeedGroup`` s
    (each carrying exactly one objective plus optional prompts). Both resolvers apply
    source limits followed by one ``max_total`` limit:

    - ``get_attack_seed_groups_async`` -- a flat ``list[AttackSeedGroup]``, sampled
      globally over all built groups.
    - ``get_attack_groups_by_dataset_async`` -- the same globally sampled groups, keyed by
      dataset name, used when a scenario fans atomic attacks out per (technique, dataset).

    Both run ``validators`` against the full resolved seed set before sampling.

    Override ``_build_attack_groups`` to change how raw seeds become attack groups
    (e.g. synthesizing a per-prompt objective). The default regroups by
    ``prompt_group_id`` via ``group_seeds_into_attack_groups``.
    """

    def _default_max_total(self) -> ResolvedDatasetLimit:
        return 5 if self._seeds is not None or self._seed_groups is not None else "all"

    def _default_max_per_dataset(self) -> ResolvedDatasetLimit:
        inline = self._seeds is not None or self._seed_groups is not None
        return 5 if not inline else "all"

    def _build_attack_groups(self, seeds: list[Seed]) -> list[AttackSeedGroup]:
        """
        Shape raw seeds into attack groups (override seam).

        The default regroups by ``prompt_group_id`` (construction validates each group has
        exactly one objective). Override to build a custom shape.

        Args:
            seeds (list[Seed]): The raw seeds to group.

        Returns:
            list[AttackSeedGroup]: The built attack groups.
        """
        return group_seeds_into_attack_groups(seeds)

    def _inline_attack_groups(self) -> list[AttackSeedGroup] | None:
        """
        Return inline attack groups when built from explicit ``seeds``/``seed_groups``.

        Returns:
            list[AttackSeedGroup] | None: The inline attack groups, or None when the
                configuration draws from ``dataset_names``.
        """
        if self._seed_groups is not None:
            return [
                group if isinstance(group, AttackSeedGroup) else AttackSeedGroup(seeds=list(group.seeds))
                for group in self._seed_groups
            ]
        if self._seeds is not None:
            return self._build_attack_groups(list(self._seeds))
        return None

    async def _build_groups_by_dataset_async(self) -> tuple[dict[str, list[AttackSeedGroup]], ResolvedDataset]:
        """
        Build attack groups keyed by dataset, plus the resolved seed set for validation.

        Inline configs preserve their explicit grouping under the ``INLINE_DATASET_NAME`` label
        (they are not flattened and regrouped). Named datasets reuse ``_collect_named_seeds_async``
        (read-only collection) and run each dataset's seeds through
        ``_build_attack_groups``.

        Returns:
            tuple[dict[str, list[AttackSeedGroup]], ResolvedDataset]: Groups keyed by
                dataset name, and the flat resolved seeds with their source kind.

        Raises:
            DatasetConstraintError: If a configured dataset yields no seeds.
        """
        inline = self._inline_attack_groups()
        if inline is not None:
            flattened = [seed for group in inline for seed in group.seeds]
            resolved = ResolvedDataset(seeds=flattened, source_kind=self.source_kind, dataset_names=())
            return {INLINE_DATASET_NAME: inline}, resolved

        seeds_by_dataset = await self._collect_named_seeds_async()
        groups_by_dataset = {name: self._build_attack_groups(seeds) for name, seeds in seeds_by_dataset.items()}
        all_seeds = [seed for seeds in seeds_by_dataset.values() for seed in seeds]
        resolved = ResolvedDataset(
            seeds=all_seeds,
            source_kind=self.source_kind,
            dataset_names=tuple(seeds_by_dataset),
        )
        return groups_by_dataset, resolved

    async def get_attack_seed_groups_async(self, *, apply_sampling: bool = True) -> list[AttackSeedGroup]:
        """
        Resolve the configured dataset into a flat ``list[AttackSeedGroup]``.

        Builds attack groups from inline data or memory, validates the full resolved
        seed set, then samples each source and the combined population.

        Args:
            apply_sampling (bool): When True (default), apply source and total sampling.
                Pass False to resolve the full, deterministic dataset with no ``random.sample``
                draw -- used on resume so the persisted objective subset can be reconstructed
                exactly rather than intersected against a fresh (divergent) sample.

        Returns:
            list[AttackSeedGroup]: The validated attack groups (sampled when ``apply_sampling``
                is True, otherwise the full resolved set).

        Raises:
            DatasetConstraintError: If a configured dataset yields no seeds, the resolved
                dataset fails validation, or no attack groups could be built.
        """
        grouped = await self.get_attack_groups_by_dataset_async(apply_sampling=apply_sampling)
        return [group for groups in grouped.values() for group in groups]

    async def get_attack_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        """
        Resolve attack groups keyed by dataset name, globally sampled.

        Inline configs resolve under the ``INLINE_DATASET_NAME`` label. Validate the
        full seed set, apply source limits, then apply ``max_total`` to the union.
        Survivors retain their source association. A source with no selected groups
        keeps its key with an empty list.

        Args:
            apply_sampling (bool): When True (default), apply source and total sampling.
                Pass False to resolve the full, deterministic dataset with no ``random.sample``
                draw -- used on resume so the persisted objective subset can be reconstructed
                exactly rather than intersected against a fresh (divergent) sample.

        Returns:
            dict[str, list[AttackSeedGroup]]: Dataset name -> attack groups (sampled when
                ``apply_sampling`` is True, otherwise the full resolved set).

        Raises:
            DatasetConstraintError: If a configured dataset yields no seeds, the resolved
                dataset fails validation, or no attack groups could be built.
        """
        selection = await self._resolve_attack_groups_async()
        sampled = selection.select(apply_sampling=apply_sampling)
        if not any(sampled.values()):
            names = ", ".join(self.dataset_names) if self.dataset_names else "<inline>"
            raise DatasetConstraintError(f"Resolved attack-group dataset is empty (datasets: {names}).")
        return sampled

    async def _resolve_attack_groups_async(self) -> _ResolvedAttackGroups:
        self.validate_configuration()
        groups, resolved = await self._build_groups_by_dataset_async()
        self.validate(resolved)
        return _ResolvedAttackGroups(configuration=self, groups=groups)

    def _sample_groups_by_dataset(
        self, groups_by_dataset: dict[str, list[AttackSeedGroup]]
    ) -> dict[str, list[AttackSeedGroup]]:
        """
        Apply source caps, then the total cap, preserving dataset keys.

        Flattens every ``(dataset_name, group)`` pair, samples up to ``max_total``
        across the union, then regroups the survivors under their originating dataset name.

        Args:
            groups_by_dataset (dict[str, list[AttackSeedGroup]]): Built groups keyed by dataset.

        Returns:
            dict[str, list[AttackSeedGroup]]: The globally sampled groups, still keyed by dataset.
        """
        limited = groups_by_dataset
        if any(self.source_limit(source.name) != "all" for source in self.sources):
            limited = {
                name: self._sample_source_groups(name=name, groups=groups) for name, groups in groups_by_dataset.items()
            }
        pairs = [(name, group) for name, groups in limited.items() for group in groups]
        result: dict[str, list[AttackSeedGroup]] = {name: [] for name in limited}
        for name, group in self._apply_max_dataset_size(pairs):
            result[name].append(group)
        return result

    def _sample_source_groups(self, *, name: str, groups: list[AttackSeedGroup]) -> list[AttackSeedGroup]:
        limit = self.source_limit(name)
        return random.sample(groups, limit) if limit != "all" and len(groups) > limit else groups


class CompoundDatasetAttackConfiguration(DatasetAttackConfiguration):
    """
    A ``DatasetAttackConfiguration`` composed of child configurations.

    Each child resolves, validates, and samples itself (with its own ``max_dataset_size``);
    this compound concatenates the results. Use it to combine datasets that need independent
    budgets or shaping -- for example "up to 4 attack groups from *each* of several datasets"
    (see ``per_dataset``), or pairing one dataset's objectives with another dataset's prompts.

    Use a compound for independent shaping or validators. Simple per-dataset budgets
    use ``DatasetSource`` instead. ``max_total`` caps the combined child selections.
    """

    def __init__(
        self,
        *,
        configurations: Sequence[DatasetAttackConfiguration],
        max_total: DatasetLimit | _Unset = _Unset.VALUE,
        max_dataset_size: DatasetLimit | _Unset = _Unset.VALUE,
        validators: Sequence[Callable[[ResolvedDataset], None]] | None = None,
    ) -> None:
        """
        Initialize a compound configuration from child configurations.

        Args:
            configurations (Sequence[DatasetAttackConfiguration]): The child configurations to
                combine; each resolves and samples independently. Must be non-empty.
            max_total (DatasetLimit | _Unset): Optional cap on the combined child selections.
            max_dataset_size (DatasetLimit | _Unset): Deprecated alias for max_total.
            validators (Sequence[Callable[[ResolvedDataset], None]] | None): Validators run
                against the combined resolved seeds, in addition to each child's validators.

        Raises:
            ValueError: If ``configurations`` is empty.
        """
        if not configurations:
            raise ValueError("CompoundDatasetAttackConfiguration requires at least one child configuration.")
        super().__init__(
            max_total=max_total, max_dataset_size=max_dataset_size, max_per_dataset="all", validators=validators
        )
        self._configurations = list(configurations)

    @property
    def has_sampling_limits(self) -> bool:
        """Whether this compound or any child can select a subset."""
        return super().has_sampling_limits or any(child.has_sampling_limits for child in self._configurations)

    def _preparation_sources(self) -> list[tuple[str, DatasetFetchPolicy]]:
        return [source for child in self._configurations for source in child._preparation_sources()]

    def with_overrides(
        self,
        *,
        sources: Sequence[DatasetSource] | _Unset = _Unset.VALUE,
        max_per_dataset: DatasetLimit | _Unset = _Unset.VALUE,
        max_total: DatasetLimit | _Unset = _Unset.VALUE,
        fetch: DatasetFetchPolicy | _Unset = _Unset.VALUE,
        filters: dict[str, list[str]] | None = None,
    ) -> Self:
        """
        Copy a compound and its children without rebuilding their custom classes.

        Returns:
            Self: The copied compound and child selection settings.

        Raises:
            DatasetConstraintError: If source replacement is requested on the compound.
        """
        if not isinstance(sources, _Unset):
            raise DatasetConstraintError("Replace named sources on individual compound children, not the compound.")
        result = super().with_overrides(max_total=max_total)
        result._configurations = [
            child.with_overrides(max_per_dataset=max_per_dataset, fetch=fetch, filters=filters)
            for child in self._configurations
        ]
        if filters is not None:
            result._filters.update({key: list(values) for key, values in filters.items()})
        return result

    @classmethod
    def per_dataset(
        cls,
        *,
        dataset_names: Sequence[str],
        max_dataset_size: DatasetLimit = "default",
        auto_fetch: bool = True,
        filters: dict[str, list[str]] | None = None,
        validators: Sequence[Callable[[ResolvedDataset], None]] | None = None,
    ) -> CompoundDatasetAttackConfiguration:
        """
        Build a compound that draws up to ``max_dataset_size`` from *each* dataset name.

        Creates one single-dataset ``DatasetAttackConfiguration`` child per name, so the budget
        applies independently to each -- the explicit, composable form of "N per dataset".

        Args:
            dataset_names (Sequence[str]): The dataset names; one child is built per name.
            max_dataset_size (DatasetLimit): Per-dataset cap applied to each child.
                Defaults to 5; pass "all" for unlimited children.
            auto_fetch (bool): Passed to each child (fetch missing datasets into memory).
            filters (dict[str, list[str]] | None): ``get_seeds`` filters applied to each child.
            validators (Sequence[Callable[[ResolvedDataset], None]] | None): Applied to each child.

        Returns:
            CompoundDatasetAttackConfiguration: The composed configuration.

        Raises:
            ValueError: If ``dataset_names`` is empty.
        """
        if not dataset_names:
            raise ValueError("per_dataset requires at least one dataset name.")
        _deprecated_argument(old="CompoundDatasetAttackConfiguration.per_dataset(...)", new="sources=...")
        return cls(
            configurations=[
                DatasetAttackConfiguration(
                    sources=[DatasetSource(name=name)],
                    max_per_dataset=max_dataset_size,
                    fetch=DatasetFetchPolicy.IF_MISSING if auto_fetch else DatasetFetchPolicy.NEVER,
                    filters=filters,
                    validators=validators,
                )
                for name in dataset_names
            ]
        )

    @property
    def dataset_names(self) -> list[str]:
        """
        The dataset names contributed by every child, in order (de-duplicated).

        Returns:
            list[str]: Aggregated child dataset names.
        """
        names: list[str] = []
        for child in self._configurations:
            for name in child.dataset_names:
                if name not in names:
                    names.append(name)
        return names

    @property
    def source_kind(self) -> DatasetSourceKind:
        """
        Whether every child is inline; otherwise the compound is treated as memory-sourced.

        Returns:
            DatasetSourceKind: ``INLINE`` only when all children are inline, else ``MEMORY``.
        """
        if all(child.source_kind is DatasetSourceKind.INLINE for child in self._configurations):
            return DatasetSourceKind.INLINE
        return DatasetSourceKind.MEMORY

    def get_size_budget(self) -> ScenarioDatasetSizeEstimate:
        """
        Combine child budgets and apply the optional overall cap without reading seeds.

        Returns:
            ScenarioDatasetSizeEstimate: A combined upper limit, or all available finite data.
        """
        budgets = [child.get_size_budget() for child in self._configurations]
        for budget in budgets:
            if isinstance(budget, IndeterminateDatasetSize):
                return budget
        if not all(isinstance(budget, BoundedDatasetSize) for budget in budgets):
            if self.max_total != "all":
                return BoundedDatasetSize(value=self.max_total)
            return AllAvailableDatasetSize()
        total = sum(budget.value for budget in budgets if isinstance(budget, BoundedDatasetSize))
        return BoundedDatasetSize(value=min(total, self.max_total) if self.max_total != "all" else total)

    def validate_configuration(self) -> None:
        """Check the compound and every child without resolving dataset contents."""
        super().validate_configuration()
        for child in self._configurations:
            child.validate_configuration()

    def size_caps_by_dataset(self) -> dict[str, list[tuple[str, int, Literal["dataset", "configuration", "compound"]]]]:
        """
        Describe child and combined caps for every contributed dataset.

        Returns:
            dict[str, list[tuple[str, int, Literal]]]: Ordered cap labels, counts, and provenance by source.
        """
        caps: dict[str, list[tuple[str, int, Literal["dataset", "configuration", "compound"]]]] = {}
        for child in self._configurations:
            for name, child_caps in child.size_caps_by_dataset().items():
                caps.setdefault(name, []).extend(child_caps)
        if self.max_total != "all":
            for name in self.dataset_names or [INLINE_DATASET_NAME]:
                caps.setdefault(name, []).append(("combined compound cap", self.max_total, "compound"))
        return caps

    def update_filters(self, *, filters: dict[str, list[str]]) -> None:
        """
        Merge filters into the compound and propagate them to every child configuration.

        The children run the actual ``get_seeds`` queries, so run-time filter overrides must
        reach each child to take effect.

        Args:
            filters (dict[str, list[str]]): Filters to merge, keyed by ``get_seeds`` kwarg name.
        """
        super().update_filters(filters=filters)
        for child in self._configurations:
            child.update_filters(filters=filters)

    async def get_attack_seed_groups_async(self, *, apply_sampling: bool = True) -> list[AttackSeedGroup]:
        """
        Concatenate every child's flat result, then validate and apply the global cap.

        Each child validates and samples itself; the combined result is validated against this
        compound's validators and capped by an optional compound ``max_dataset_size``.

        Args:
            apply_sampling (bool): When True (default), sample both each child and the combined
                result under ``max_dataset_size``. Pass False to resolve the full, deterministic
                dataset with no sampling at any level -- used on resume (propagated to children).

        Returns:
            list[AttackSeedGroup]: The combined, validated attack groups (capped when
                ``apply_sampling`` is True).

        Raises:
            DatasetConstraintError: If a child yields nothing, or the combined result fails validation.
        """
        grouped = await self.get_attack_groups_by_dataset_async(apply_sampling=apply_sampling)
        return [group for groups in grouped.values() for group in groups]

    async def get_attack_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        """
        Merge each child's by-dataset result, validate, then apply the global cap across the union.

        Args:
            apply_sampling (bool): When True (default), sample both each child and the merged
                union under ``max_dataset_size``. Pass False to resolve the full, deterministic
                dataset with no sampling at any level -- used on resume (propagated to children).

        Returns:
            dict[str, list[AttackSeedGroup]]: Combined groups keyed by dataset name.

        Raises:
            DatasetConstraintError: If a child yields nothing, or the combined result fails validation.
        """
        return await super().get_attack_groups_by_dataset_async(apply_sampling=apply_sampling)

    async def _resolve_attack_groups_async(self) -> _ResolvedAttackGroups:
        self.validate_configuration()
        children = tuple([await child._resolve_attack_groups_async() for child in self._configurations])
        merged: dict[str, list[AttackSeedGroup]] = {}
        for child in children:
            for name, groups in child.groups.items():
                merged.setdefault(name, []).extend(groups)
        self.validate(self._resolved_from_groups([group for groups in merged.values() for group in groups]))
        return _ResolvedAttackGroups(configuration=self, groups=merged, children=children)

    def _resolved_from_groups(self, groups: list[AttackSeedGroup]) -> ResolvedDataset:
        """
        Build a ResolvedDataset over the combined groups for compound-level validation.

        Args:
            groups (list[AttackSeedGroup]): The combined attack groups.

        Returns:
            ResolvedDataset: Carries the flattened seeds, source kind, and aggregated names.
        """
        seeds = [seed for group in groups for seed in group.seeds]
        return ResolvedDataset(seeds=seeds, source_kind=self.source_kind, dataset_names=tuple(self.dataset_names))
