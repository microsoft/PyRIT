# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset configuration for scenarios.

``DatasetConfiguration`` is the object a scenario uses to say "where do my seeds come
from." ``DatasetAttackConfiguration`` -- the configuration most scenarios use -- groups
the resolved seeds into ``SeedAttackGroup`` s (each carrying exactly one objective plus
optional prompts).

Constraints are expressed through a single mechanism: ``validators``. Each validator is a
``Callable[[ResolvedDataset], None]`` that raises ``DatasetConstraintError`` on violation.
Validators run against the fully resolved dataset (before ``max_dataset_size`` sampling),
so they describe the dataset itself, not the sampled subset. The ``ResolvedDataset`` they
receive also carries the ``DatasetSourceKind`` (inline vs from memory) and the contributing
``dataset_names``, which lets a scenario require or forbid inline seeds -- useful for CLI
flags such as ``--objectives`` -- restrict which datasets it will resolve from, or require a
particular seed type (e.g. ``require_seed_type(SeedObjective)``).

Memory is the source of truth. When a configured dataset name is not yet in memory and
``auto_fetch`` is enabled (the default), the resolver transparently fetches the dataset
from the registered ``SeedDatasetProvider`` into memory. If a configured dataset
name still yields nothing, the resolver raises loudly rather than silently skipping it.
Inline configs (``seeds=`` / ``seed_groups=``) never touch memory.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING, Any, TypeVar, cast

from pyrit.memory import CentralMemory
from pyrit.models import Seed, SeedAttackGroup, SeedGroup, group_seeds_into_attack_groups

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pyrit.memory import MemoryInterface

# Dataset-name label that inline ``seeds`` / ``seed_groups`` carry in by-dataset views, since
# they have no real dataset name. Inline and named sources are mutually exclusive, so this
# never collides with a configured dataset name.
INLINE_DATASET_NAME = "inline"

# Internal helper TypeVar for size-capping any homogeneous list.
_ItemT = TypeVar("_ItemT")


class DatasetSourceKind(Enum):
    """
    How a ``DatasetConfiguration``'s seeds were sourced.

    Only two cases matter to validators: seeds supplied inline by the caller, versus
    seeds loaded from memory by dataset name (auto-fetched into memory first when
    missing). This lets a constraint require or forbid inline data -- e.g. a CLI
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
        seeds (Sequence[Seed]): The resolved seeds (before ``max_dataset_size`` sampling).
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

    This base class handles resolution, fetching, validation, and sampling.
    ``DatasetAttackConfiguration`` is the concrete subclass most scenarios use; it groups
    the resolved seeds into ``SeedAttackGroup`` s. A configuration draws from exactly one
    source:

    - ``seeds`` -- an explicit, inline list of seeds (never touches memory).
    - ``seed_groups`` -- explicit, inline seed groups (never touches memory).
    - ``dataset_names`` -- names looked up in memory; missing names are fetched from the
      registered ``SeedDatasetProvider`` when ``auto_fetch`` is enabled.

    Resolution reads memory (the source of truth) and, per dataset name, fetches from the
    provider when missing and ``auto_fetch`` is set. If a configured name still yields no
    seeds, ``_collect_seeds_for_dataset_async`` raises ``DatasetConstraintError`` -- failures
    are loud, not silently skipped.

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
        dataset_names: list[str] | None = None,
        max_dataset_size: int | None = None,
        filters: dict[str, list[str]] | None = None,
        validators: Sequence[Callable[[ResolvedDataset], None]] | None = None,
        auto_fetch: bool = True,
    ) -> None:
        """
        Initialize a DatasetConfiguration.

        Args:
            seeds (Sequence[Seed] | None): Explicit, inline seeds (never touches memory).
            seed_groups (list[SeedGroup] | None): Explicit, inline seed groups (never
                touches memory).
            dataset_names (list[str] | None): Names of datasets to load from memory.
            max_dataset_size (int | None): If set, randomly samples up to this many items
                from the resolved dataset (without replacement).
            filters (dict[str, list[str]] | None): Filters passed to ``MemoryInterface.get_seeds``
                when resolving named datasets (e.g. ``{"harm_categories": ["cyber"]}``).
                Applied before ``max_dataset_size`` sampling; ignored for inline seeds.
            validators (Sequence[Callable[[ResolvedDataset], None]] | None): Constraint
                callbacks run against the resolved dataset; each raises on violation. These
                are appended to the subclass's ``_default_validators``.
            auto_fetch (bool): When True (default), a configured dataset name that is not
                in memory is fetched from the registered ``SeedDatasetProvider`` into
                memory before resolving. Set False for strict "must already be in memory".

        Raises:
            ValueError: If more than one of seeds/seed_groups/dataset_names is set.
            ValueError: If max_dataset_size is less than 1.
        """
        sources = [src for src in (seeds, seed_groups, dataset_names) if src is not None]
        if len(sources) > 1:
            raise ValueError(
                "Only one of 'seeds', 'seed_groups', or 'dataset_names' can be set. "
                "Use 'seeds'/'seed_groups' to provide inline data, or 'dataset_names' to load from memory."
            )

        if max_dataset_size is not None and max_dataset_size < 1:
            raise ValueError("'max_dataset_size' must be a positive integer (>= 1).")

        self._seeds = list(seeds) if seeds is not None else None
        self._seed_groups = list(seed_groups) if seed_groups is not None else None
        self._dataset_names = list(dataset_names) if dataset_names is not None else None
        self.max_dataset_size = max_dataset_size
        self._filters: dict[str, list[str]] = dict(filters or {})
        self._validators: list[Callable[[ResolvedDataset], None]] = [
            *self._default_validators(),
            *(list(validators) if validators else []),
        ]
        self._auto_fetch = auto_fetch

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
        return list(self._dataset_names or [])

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
        self._filters = {**self._filters, **filters}

    # =========================================================================
    # Resolution helpers
    # =========================================================================

    async def _collect_named_seeds_async(self) -> dict[str, list[Seed]]:
        """
        Collect seeds for each configured dataset name, keyed by name.

        Each name is read from memory and -- when empty and ``auto_fetch`` is set -- fetched
        from the provider; a name that still yields nothing raises loudly.

        Returns:
            dict[str, list[Seed]]: Dataset name -> seeds, in configuration order (every value
                is non-empty).

        Raises:
            DatasetConstraintError: If any configured dataset yields no seeds.
        """
        result: dict[str, list[Seed]] = {}
        for name in self._dataset_names or []:
            result[name] = await self._collect_seeds_for_dataset_async(dataset_name=name)
        return result

    async def _collect_seeds_for_dataset_async(self, *, dataset_name: str) -> list[Seed]:
        """
        Collect seeds for a single dataset name, fetching from the provider if needed.

        Args:
            dataset_name (str): The dataset name to load.

        Returns:
            list[Seed]: The seeds for ``dataset_name``.

        Raises:
            DatasetConstraintError: If the dataset yields no seeds even after auto-fetch, or
                if auto-fetch itself fails (the provider error is chained as the cause).
        """
        found = list(self._memory.get_seeds(dataset_name=dataset_name, **self._get_seeds_filters))
        if not found and self._auto_fetch:
            try:
                await self._fetch_dataset_async(dataset_name=dataset_name)
            except Exception as exc:
                raise DatasetConstraintError(
                    f"Dataset '{dataset_name}' could not be loaded: auto-fetch from the registered provider failed."
                ) from exc
            found = list(self._memory.get_seeds(dataset_name=dataset_name, **self._get_seeds_filters))
        if not found:
            if self._filters and self._memory.get_seeds(dataset_name=dataset_name):
                raise DatasetConstraintError(
                    f"Dataset '{dataset_name}' has seeds, but none match the configured filters {self._filters}."
                )
            hint = (
                "auto-fetch from the registered provider did not populate it"
                if self._auto_fetch
                else "auto_fetch is disabled"
            )
            raise DatasetConstraintError(
                f"Dataset '{dataset_name}' could not be loaded: no seeds found in memory and {hint}."
            )
        return found

    async def _fetch_dataset_async(self, *, dataset_name: str) -> None:
        """
        Populate memory from the registered provider for a single dataset (private).

        An unregistered name populates nothing and falls through to the caller's loud
        empty-result handling. Provider errors (enumeration or fetch) propagate so the
        caller can surface the root cause. Never samples or validates -- it only adds to
        memory.

        Args:
            dataset_name (str): The dataset name to fetch.
        """
        # Local import to avoid an import cycle at package init time.
        from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider

        registered = set(await SeedDatasetProvider.get_all_dataset_names_async())
        if dataset_name not in registered:
            return

        datasets = await SeedDatasetProvider.fetch_datasets_async(dataset_names=[dataset_name])
        await self._memory.add_seed_datasets_to_memory_async(datasets=datasets, added_by="DatasetConfiguration")

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
        Apply ``max_dataset_size`` sampling without replacement.

        Args:
            items (list[_ItemT]): The items to potentially sample from.

        Returns:
            list[_ItemT]: The original list, or a random sample of up to
                ``max_dataset_size`` unique items.
        """
        if self.max_dataset_size is None or len(items) <= self.max_dataset_size:
            return items
        return random.sample(items, self.max_dataset_size)


class DatasetAttackConfiguration(DatasetConfiguration):
    """
    A ``DatasetConfiguration`` that groups resolved seeds into attack groups.

    This is the default most scenarios use: scenarios run over ``SeedAttackGroup`` s
    (each carrying exactly one objective plus optional prompts). ``max_dataset_size`` is a
    single global budget for the configuration; both resolvers apply it the same way:

    - ``get_seed_attack_groups_async`` -- a flat ``list[SeedAttackGroup]``, sampled
      globally over all built groups.
    - ``get_attack_groups_by_dataset_async`` -- the same globally sampled groups, keyed by
      dataset name, used when a scenario fans atomic attacks out per (technique, dataset).

    To draw an independent budget from *each* of several datasets (the old "N per dataset"
    behavior), compose one child per dataset with ``CompoundDatasetAttackConfiguration`` --
    e.g. ``CompoundDatasetAttackConfiguration.per_dataset(dataset_names=[...], max_dataset_size=4)``
    -- rather than relying on a single config to special-case dataset names.

    Both run ``validators`` against the full resolved seed set before sampling.

    Override ``_build_attack_groups`` to change how raw seeds become attack groups
    (e.g. synthesizing a per-prompt objective). The default regroups by
    ``prompt_group_id`` via ``group_seeds_into_attack_groups``.
    """

    def _build_attack_groups(self, seeds: list[Seed]) -> list[SeedAttackGroup]:
        """
        Shape raw seeds into attack groups (override seam).

        The default regroups by ``prompt_group_id`` (construction validates each group has
        exactly one objective). Override to build a custom shape.

        Args:
            seeds (list[Seed]): The raw seeds to group.

        Returns:
            list[SeedAttackGroup]: The built attack groups.
        """
        return group_seeds_into_attack_groups(seeds)

    def _inline_attack_groups(self) -> list[SeedAttackGroup] | None:
        """
        Return inline attack groups when built from explicit ``seeds``/``seed_groups``.

        Returns:
            list[SeedAttackGroup] | None: The inline attack groups, or None when the
                configuration draws from ``dataset_names``.
        """
        if self._seed_groups is not None:
            return [
                group if isinstance(group, SeedAttackGroup) else SeedAttackGroup(seeds=list(group.seeds))
                for group in self._seed_groups
            ]
        if self._seeds is not None:
            return self._build_attack_groups(list(self._seeds))
        return None

    async def _build_groups_by_dataset_async(self) -> tuple[dict[str, list[SeedAttackGroup]], ResolvedDataset]:
        """
        Build attack groups keyed by dataset, plus the resolved seed set for validation.

        Inline configs preserve their explicit grouping under the ``INLINE_DATASET_NAME`` label
        (they are not flattened and regrouped). Named datasets reuse ``_collect_named_seeds_async``
        (auto-fetch + loud empty handling) and run each dataset's seeds through
        ``_build_attack_groups``.

        Returns:
            tuple[dict[str, list[SeedAttackGroup]], ResolvedDataset]: Groups keyed by
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

    async def get_seed_attack_groups_async(self) -> list[SeedAttackGroup]:
        """
        Resolve the configured dataset into a flat ``list[SeedAttackGroup]``.

        Builds attack groups (inline or from memory, auto-fetching missing datasets),
        validates the full resolved seed set, then samples ``max_dataset_size`` globally
        over all built groups.

        Returns:
            list[SeedAttackGroup]: The validated, sampled attack groups.

        Raises:
            DatasetConstraintError: If a configured dataset yields no seeds, the resolved
                dataset fails validation, or no attack groups could be built.
        """
        groups_by_dataset, resolved = await self._build_groups_by_dataset_async()
        self.validate(resolved)
        groups = [group for groups in groups_by_dataset.values() for group in groups]
        groups = self._apply_max_dataset_size(groups)
        if not groups:
            names = ", ".join(self._dataset_names) if self._dataset_names else "<inline>"
            raise DatasetConstraintError(f"Resolved attack-group dataset is empty (datasets: {names}).")
        return groups

    async def get_attack_groups_by_dataset_async(self) -> dict[str, list[SeedAttackGroup]]:
        """
        Resolve attack groups keyed by dataset name, globally sampled.

        Inline configs resolve under the ``INLINE_DATASET_NAME`` label. Builds attack groups
        (auto-fetching missing datasets), validates the full resolved seed set, then applies
        ``max_dataset_size`` as one global budget across all datasets -- the survivors stay
        keyed by their originating dataset. For an independent budget per dataset, compose
        ``CompoundDatasetAttackConfiguration.per_dataset(...)`` instead.

        Returns:
            dict[str, list[SeedAttackGroup]]: Dataset name -> sampled attack groups.

        Raises:
            DatasetConstraintError: If a configured dataset yields no seeds, the resolved
                dataset fails validation, or no attack groups could be built.
        """
        groups_by_dataset, resolved = await self._build_groups_by_dataset_async()
        self.validate(resolved)
        result = {name: groups for name, groups in self._sample_groups_by_dataset(groups_by_dataset).items() if groups}
        if not result:
            names = ", ".join(self._dataset_names) if self._dataset_names else "<inline>"
            raise DatasetConstraintError(f"Resolved attack-group dataset is empty (datasets: {names}).")
        return result

    def _sample_groups_by_dataset(
        self, groups_by_dataset: dict[str, list[SeedAttackGroup]]
    ) -> dict[str, list[SeedAttackGroup]]:
        """
        Apply ``max_dataset_size`` as one global budget across datasets, preserving keys.

        Flattens every ``(dataset_name, group)`` pair, samples up to ``max_dataset_size``
        across the union, then regroups the survivors under their originating dataset name.

        Args:
            groups_by_dataset (dict[str, list[SeedAttackGroup]]): Built groups keyed by dataset.

        Returns:
            dict[str, list[SeedAttackGroup]]: The globally sampled groups, still keyed by dataset.
        """
        pairs = [(name, group) for name, groups in groups_by_dataset.items() for group in groups]
        result: dict[str, list[SeedAttackGroup]] = {}
        for name, group in self._apply_max_dataset_size(pairs):
            result.setdefault(name, []).append(group)
        return result


class CompoundDatasetAttackConfiguration(DatasetAttackConfiguration):
    """
    A ``DatasetAttackConfiguration`` composed of child configurations.

    Each child resolves, validates, and samples itself (with its own ``max_dataset_size``);
    this compound concatenates the results. Use it to combine datasets that need independent
    budgets or shaping -- for example "up to 4 attack groups from *each* of several datasets"
    (see ``per_dataset``), or pairing one dataset's objectives with another dataset's prompts.

    A single ``DatasetAttackConfiguration`` applies ``max_dataset_size`` as one global budget;
    per-dataset budgets are expressed by composing one child per dataset rather than by
    special-casing dataset names inside a single configuration. An optional compound-level
    ``max_dataset_size`` caps the combined result on top of each child's own sampling.
    """

    def __init__(
        self,
        *,
        configurations: Sequence[DatasetAttackConfiguration],
        max_dataset_size: int | None = None,
        validators: Sequence[Callable[[ResolvedDataset], None]] | None = None,
    ) -> None:
        """
        Initialize a compound configuration from child configurations.

        Args:
            configurations (Sequence[DatasetAttackConfiguration]): The child configurations to
                combine; each resolves and samples independently. Must be non-empty.
            max_dataset_size (int | None): Optional cap applied to the *combined* result, on
                top of each child's own sampling.
            validators (Sequence[Callable[[ResolvedDataset], None]] | None): Validators run
                against the combined resolved seeds, in addition to each child's validators.

        Raises:
            ValueError: If ``configurations`` is empty.
        """
        if not configurations:
            raise ValueError("CompoundDatasetAttackConfiguration requires at least one child configuration.")
        super().__init__(max_dataset_size=max_dataset_size, validators=validators)
        self._configurations = list(configurations)

    @classmethod
    def per_dataset(
        cls,
        *,
        dataset_names: Sequence[str],
        max_dataset_size: int | None = None,
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
            max_dataset_size (int | None): Per-dataset cap applied to each child.
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
        return cls(
            configurations=[
                DatasetAttackConfiguration(
                    dataset_names=[name],
                    max_dataset_size=max_dataset_size,
                    auto_fetch=auto_fetch,
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

    async def get_seed_attack_groups_async(self) -> list[SeedAttackGroup]:
        """
        Concatenate every child's flat result, then validate and apply the global cap.

        Each child validates and samples itself; the combined result is validated against this
        compound's validators and capped by an optional compound ``max_dataset_size``.

        Returns:
            list[SeedAttackGroup]: The combined, validated, capped attack groups.

        Raises:
            DatasetConstraintError: If a child yields nothing, or the combined result fails validation.
        """
        groups: list[SeedAttackGroup] = []
        for child in self._configurations:
            groups.extend(await child.get_seed_attack_groups_async())
        self.validate(self._resolved_from_groups(groups))
        return self._apply_max_dataset_size(groups)

    async def get_attack_groups_by_dataset_async(self) -> dict[str, list[SeedAttackGroup]]:
        """
        Merge each child's by-dataset result, validate, then apply the global cap across the union.

        Returns:
            dict[str, list[SeedAttackGroup]]: Combined groups keyed by dataset name.

        Raises:
            DatasetConstraintError: If a child yields nothing, or the combined result fails validation.
        """
        merged: dict[str, list[SeedAttackGroup]] = {}
        for child in self._configurations:
            for name, groups in (await child.get_attack_groups_by_dataset_async()).items():
                merged.setdefault(name, []).extend(groups)
        self.validate(self._resolved_from_groups([group for groups in merged.values() for group in groups]))
        return self._sample_groups_by_dataset(merged)

    def _resolved_from_groups(self, groups: list[SeedAttackGroup]) -> ResolvedDataset:
        """
        Build a ResolvedDataset over the combined groups for compound-level validation.

        Args:
            groups (list[SeedAttackGroup]): The combined attack groups.

        Returns:
            ResolvedDataset: Carries the flattened seeds, source kind, and aggregated names.
        """
        seeds = [seed for group in groups for seed in group.seeds]
        return ResolvedDataset(seeds=seeds, source_kind=self.source_kind, dataset_names=tuple(self.dataset_names))
