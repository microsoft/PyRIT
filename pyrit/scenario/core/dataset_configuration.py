# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Dataset configuration for scenarios.

``DatasetConfiguration`` is the object a scenario uses to say "where do my seeds come
from." The base class is generic in its seed type and resolves to a flat ``list[SeedT]``
via ``get_seeds_async``. Subclasses narrow or reshape that result:

- ``DatasetObjectiveConfiguration`` -- requires every seed to be a ``SeedObjective``.
- ``DatasetPromptConfiguration`` -- requires every seed to be a ``SeedPrompt``.
- ``DatasetAttackConfiguration`` -- groups seeds into ``SeedAttackGroup`` s (the
  default most scenarios use).

Constraints are expressed through a single mechanism: ``validators``. Each validator is a
``Callable[[ResolvedDataset], None]`` that raises ``DatasetConstraintError`` on violation.
A typed subclass preloads its type check via ``_default_validators`` rather than overriding
``validate``. Validators run against the fully resolved dataset (before ``max_dataset_size``
sampling), so they describe the dataset itself, not the sampled subset. The ``ResolvedDataset``
they receive also carries the ``DatasetSourceKind`` (inline vs from memory), which lets a
scenario require or forbid inline seeds -- useful for CLI flags such as ``--objectives``.

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
from typing import TYPE_CHECKING, Generic, TypeVar, cast

from pyrit.common.deprecation import print_deprecation_message
from pyrit.memory import CentralMemory
from pyrit.models import (
    Seed,
    SeedAttackGroup,
    SeedGroup,
    SeedObjective,
    SeedPrompt,
    group_seeds_into_attack_groups,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pyrit.memory import MemoryInterface

# Key used when seed_groups are provided directly (not from a named dataset)
EXPLICIT_SEED_GROUPS_KEY = "_explicit_seed_groups"

# Version in which the deprecated legacy getters will be removed (current ver: 0.15.0.dev0).
_LEGACY_REMOVED_IN = "0.17.0"

# Internal helper TypeVar for size-capping any homogeneous list.
_ItemT = TypeVar("_ItemT")

# The seed type a configuration resolves to (objective, prompt, or the base ``Seed``).
SeedT = TypeVar("SeedT", bound=Seed)


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

    Passed to every validator so a constraint can inspect both the seeds and how they
    were supplied (inline vs named dataset).

    Args:
        seeds (Sequence[Seed]): The resolved seeds (before ``max_dataset_size`` sampling).
        source_kind (DatasetSourceKind): How the configuration was sourced.
    """

    seeds: Sequence[Seed]
    source_kind: DatasetSourceKind

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


class DatasetConfiguration(Generic[SeedT]):
    """
    Configuration describing where a scenario's seeds come from.

    The base class is generic in its seed type and resolves to a flat ``list[SeedT]`` via
    ``get_seeds_async``. A configuration draws from exactly one source:

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

    The legacy getters (``get_seed_groups`` / ``get_all_seed_attack_groups`` / ...) are
    deprecated and will be removed in 0.17.0; prefer ``get_seeds_async`` and the
    typed subclasses.
    """

    def __init__(
        self,
        *,
        seeds: Sequence[Seed] | None = None,
        seed_groups: list[SeedGroup] | None = None,
        dataset_names: list[str] | None = None,
        max_dataset_size: int | None = None,
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
        self._validators: list[Callable[[ResolvedDataset], None]] = [
            *self._default_validators(),
            *(list(validators) if validators else []),
        ]
        self._auto_fetch = auto_fetch

    def _default_validators(self) -> list[Callable[[ResolvedDataset], None]]:
        """
        Return validators a subclass always applies, prepended to user-supplied ``validators``.

        The base requires a non-empty resolved dataset. Typed subclasses extend this to
        enforce a seed type (e.g. ``require_seed_type(SeedObjective)``) -- they should call
        ``super()._default_validators()`` rather than overriding ``validate``.

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

    # =========================================================================
    # Resolver pipeline (the public entry point)
    # =========================================================================

    async def get_seeds_async(self) -> list[SeedT]:
        """
        Resolve the configured dataset into a flat ``list[SeedT]``.

        Pipeline: collect seeds by dataset (inline data, or from memory -- fetching missing
        datasets from the provider when ``auto_fetch`` is set), flatten, validate the full
        resolved dataset, then sample (``max_dataset_size``). Validation runs before
        sampling so validators describe the dataset itself, not the sampled subset.

        Returns:
            list[SeedT]: The resolved, validated, sampled seeds.

        Raises:
            DatasetConstraintError: If a configured dataset yields no seeds, or the
                resolved dataset fails validation.
        """
        by_dataset = await self._collect_seeds_by_dataset_async()
        seeds: list[Seed] = [seed for group in by_dataset.values() for seed in group]
        self.validate(ResolvedDataset(seeds=seeds, source_kind=self.source_kind))
        seeds = self._apply_max_dataset_size(seeds)
        return cast("list[SeedT]", seeds)

    def _inline_seeds(self) -> list[Seed] | None:
        """
        Return inline seeds when the configuration was built from explicit data.

        Returns:
            list[Seed] | None: The inline seeds (flattening ``seed_groups`` when present),
                or None when the configuration draws from ``dataset_names``.
        """
        if self._seeds is not None:
            return list(self._seeds)
        if self._seed_groups is not None:
            return [seed for group in self._seed_groups for seed in group.seeds]
        return None

    async def _collect_seeds_by_dataset_async(self) -> dict[str, list[Seed]]:
        """
        Collect seeds keyed by dataset name (inline data collapses to a single reserved key).

        Inline configs resolve under ``EXPLICIT_SEED_GROUPS_KEY``. For named datasets, each
        name is read from memory and -- when empty and ``auto_fetch`` is set -- fetched from
        the provider; a name that still yields nothing raises loudly.

        Returns:
            dict[str, list[Seed]]: Dataset name -> seeds (every value is non-empty).

        Raises:
            DatasetConstraintError: If any configured dataset yields no seeds.
            ValueError: If a configured dataset name collides with the reserved key.
        """
        inline = self._inline_seeds()
        if inline is not None:
            return {EXPLICIT_SEED_GROUPS_KEY: inline}

        result: dict[str, list[Seed]] = {}
        for name in self._dataset_names or []:
            if name == EXPLICIT_SEED_GROUPS_KEY:
                raise ValueError(
                    f"Dataset name '{EXPLICIT_SEED_GROUPS_KEY}' is reserved for internal use. "
                    "Please rename your dataset."
                )
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
        found = list(self._memory.get_seeds(dataset_name=dataset_name))
        if not found and self._auto_fetch:
            try:
                await self._fetch_dataset_async(dataset_name=dataset_name)
            except Exception as exc:
                raise DatasetConstraintError(
                    f"Dataset '{dataset_name}' could not be loaded: auto-fetch from the registered provider failed."
                ) from exc
            found = list(self._memory.get_seeds(dataset_name=dataset_name))
        if not found:
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

    # =========================================================================
    # Legacy getters (deprecated; removed in 0.17.0)
    # =========================================================================

    def get_seed_groups(self) -> dict[str, list[SeedGroup]]:
        """
        Resolve and return seed groups keyed by dataset (deprecated).

        Returns:
            dict[str, list[SeedGroup]]: Dataset name -> seed groups, sampled per dataset.

        Raises:
            ValueError: If no seed groups could be resolved from the configuration.
        """
        print_deprecation_message(
            old_item="DatasetConfiguration.get_seed_groups",
            new_item="DatasetConfiguration.get_seeds_async",
            removed_in=_LEGACY_REMOVED_IN,
        )
        return self._get_seed_groups()

    def _get_seed_groups(self) -> dict[str, list[SeedGroup]]:
        """
        Resolve and return seed groups keyed by dataset (legacy implementation).

        Returns:
            dict[str, list[SeedGroup]]: Dataset name -> seed groups, sampled per dataset.

        Raises:
            ValueError: If no seed groups could be resolved from the configuration.
        """
        result: dict[str, list[SeedGroup]] = {}

        if self._seed_groups is not None:
            sampled = self._apply_max_dataset_size(list(self._seed_groups))
            if sampled:
                result[EXPLICIT_SEED_GROUPS_KEY] = sampled
        elif self._dataset_names is not None:
            for name in self._dataset_names:
                if name == EXPLICIT_SEED_GROUPS_KEY:
                    raise ValueError(
                        f"Dataset name '{EXPLICIT_SEED_GROUPS_KEY}' is reserved for internal use. "
                        "Please rename your dataset."
                    )
                loaded = self._load_seed_groups_for_dataset(dataset_name=name)
                if loaded:
                    result[name] = self._apply_max_dataset_size(loaded)

        if not result:
            raise ValueError("DatasetConfiguration has no seed_groups. Set seed_groups or dataset_names.")

        return result

    def _load_seed_groups_for_dataset(self, *, dataset_name: str) -> list[SeedGroup]:
        """
        Load seed groups for a single dataset from memory (legacy override hook).

        Args:
            dataset_name (str): The dataset name to load.

        Returns:
            list[SeedGroup]: Seed groups loaded from memory, or empty list if none found.
        """
        return list(self._memory.get_seed_groups(dataset_name=dataset_name) or [])

    def get_all_seed_groups(self) -> list[SeedGroup]:
        """
        Resolve and return all seed groups as a flat list (deprecated).

        Returns:
            list[SeedGroup]: All resolved seed groups across datasets.
        """
        print_deprecation_message(
            old_item="DatasetConfiguration.get_all_seed_groups",
            new_item="DatasetConfiguration.get_seeds_async",
            removed_in=_LEGACY_REMOVED_IN,
        )
        all_groups: list[SeedGroup] = []
        for groups in self._get_seed_groups().values():
            all_groups.extend(groups)
        return all_groups

    def get_seed_attack_groups(self) -> dict[str, list[SeedAttackGroup]]:
        """
        Resolve and return seed groups as SeedAttackGroups, keyed by dataset (deprecated).

        Returns:
            dict[str, list[SeedAttackGroup]]: Dataset name -> seed attack groups.
        """
        print_deprecation_message(
            old_item="DatasetConfiguration.get_seed_attack_groups",
            new_item="DatasetAttackConfiguration.get_attack_groups_by_dataset_async",
            removed_in=_LEGACY_REMOVED_IN,
        )
        return self._get_seed_attack_groups()

    def _get_seed_attack_groups(self) -> dict[str, list[SeedAttackGroup]]:
        """
        Resolve and return seed groups as SeedAttackGroups, keyed by dataset (legacy impl).

        Returns:
            dict[str, list[SeedAttackGroup]]: Dataset name -> seed attack groups.
        """
        result: dict[str, list[SeedAttackGroup]] = {}
        for dataset_name, groups in self._get_seed_groups().items():
            result[dataset_name] = [SeedAttackGroup(seeds=list(sg.seeds)) for sg in groups]
        return result

    def get_all_seed_attack_groups(self) -> list[SeedAttackGroup]:
        """
        Resolve and return all seed groups as SeedAttackGroups in a flat list (deprecated).

        Returns:
            list[SeedAttackGroup]: All resolved seed attack groups across datasets.
        """
        print_deprecation_message(
            old_item="DatasetConfiguration.get_all_seed_attack_groups",
            new_item="DatasetAttackConfiguration.get_seed_attack_groups_async",
            removed_in=_LEGACY_REMOVED_IN,
        )
        all_groups: list[SeedAttackGroup] = []
        for groups in self._get_seed_attack_groups().values():
            all_groups.extend(groups)
        return all_groups

    def get_default_dataset_names(self) -> list[str]:
        """
        Get the list of default dataset names for this configuration (deprecated).

        Returns:
            list[str]: Dataset names, or empty list if using inline seeds.
        """
        print_deprecation_message(
            old_item="DatasetConfiguration.get_default_dataset_names",
            new_item="DatasetConfiguration.dataset_names",
            removed_in=_LEGACY_REMOVED_IN,
        )
        return self.dataset_names

    def get_all_seeds(self) -> list[Seed]:
        """
        Load all seeds from memory for all configured datasets (deprecated).

        Returns:
            list[Seed]: Seeds from all configured datasets (sampled per dataset).

        Raises:
            ValueError: If no dataset names are configured.
        """
        print_deprecation_message(
            old_item="DatasetConfiguration.get_all_seeds",
            new_item="DatasetConfiguration.get_seeds_async",
            removed_in=_LEGACY_REMOVED_IN,
        )
        if self._dataset_names is None:
            raise ValueError("No dataset names configured. Set dataset_names to use get_all_seeds.")

        all_seeds: list[Seed] = []
        for dataset_name in self._dataset_names:
            seeds = list(self._memory.get_seeds(dataset_name=dataset_name))
            all_seeds.extend(self._apply_max_dataset_size(seeds))
        return all_seeds


class DatasetObjectiveConfiguration(DatasetConfiguration[SeedObjective]):
    """
    A ``DatasetConfiguration`` that requires every resolved seed to be an objective.

    Use when a scenario consumes objectives directly. ``get_seeds_async`` returns the
    seeds as usual; a default ``require_seed_type(SeedObjective)`` validator enforces the type.
    """

    def _default_validators(self) -> list[Callable[[ResolvedDataset], None]]:
        """
        Require a non-empty dataset of ``SeedObjective`` items.

        Returns:
            list[Callable[[ResolvedDataset], None]]: The base defaults plus the seed-type validator.
        """
        return [*super()._default_validators(), require_seed_type(SeedObjective)]


class DatasetPromptConfiguration(DatasetConfiguration[SeedPrompt]):
    """
    A ``DatasetConfiguration`` that requires every resolved seed to be a prompt.

    Use when a scenario consumes prompts directly. ``get_seeds_async`` returns the
    seeds as usual; a default ``require_seed_type(SeedPrompt)`` validator enforces the type.
    """

    def _default_validators(self) -> list[Callable[[ResolvedDataset], None]]:
        """
        Require a non-empty dataset of ``SeedPrompt`` items.

        Returns:
            list[Callable[[ResolvedDataset], None]]: The base defaults plus the seed-type validator.
        """
        return [*super()._default_validators(), require_seed_type(SeedPrompt)]


class DatasetAttackConfiguration(DatasetConfiguration[Seed]):
    """
    A ``DatasetConfiguration`` that groups resolved seeds into attack groups.

    This is the default most scenarios use: scenarios run over ``SeedAttackGroup`` s
    (each carrying exactly one objective plus optional prompts). Two resolvers are
    provided, differing only in how ``max_dataset_size`` is applied:

    - ``get_seed_attack_groups_async`` -- a flat ``list[SeedAttackGroup]``, sampled
      globally over all built groups.
    - ``get_attack_groups_by_dataset_async`` -- groups keyed by dataset name (sampled
      per dataset), used when a scenario fans atomic attacks out per (technique, dataset).

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

        Inline configs preserve their explicit grouping under ``EXPLICIT_SEED_GROUPS_KEY``
        (they are not flattened and regrouped). Named datasets reuse
        ``_collect_seeds_by_dataset_async`` (auto-fetch + loud empty handling) and run each
        dataset's seeds through ``_build_attack_groups``.

        Returns:
            tuple[dict[str, list[SeedAttackGroup]], ResolvedDataset]: Groups keyed by
                dataset name, and the flat resolved seeds with their source kind.

        Raises:
            DatasetConstraintError: If a configured dataset yields no seeds.
            ValueError: If a configured dataset name collides with the reserved key.
        """
        inline = self._inline_attack_groups()
        if inline is not None:
            flattened = [seed for group in inline for seed in group.seeds]
            return {EXPLICIT_SEED_GROUPS_KEY: inline}, ResolvedDataset(seeds=flattened, source_kind=self.source_kind)

        seeds_by_dataset = await self._collect_seeds_by_dataset_async()
        groups_by_dataset = {name: self._build_attack_groups(seeds) for name, seeds in seeds_by_dataset.items()}
        all_seeds = [seed for seeds in seeds_by_dataset.values() for seed in seeds]
        return groups_by_dataset, ResolvedDataset(seeds=all_seeds, source_kind=self.source_kind)

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
        Resolve attack groups keyed by dataset name, sampled per dataset.

        Inline configs resolve under the ``EXPLICIT_SEED_GROUPS_KEY`` key. Builds attack
        groups (auto-fetching missing datasets), validates the full resolved seed set, then
        samples ``max_dataset_size`` per dataset independently.

        Returns:
            dict[str, list[SeedAttackGroup]]: Dataset name -> sampled attack groups.

        Raises:
            DatasetConstraintError: If a configured dataset yields no seeds, the resolved
                dataset fails validation, or no attack groups could be built.
        """
        groups_by_dataset, resolved = await self._build_groups_by_dataset_async()
        self.validate(resolved)
        result = {name: self._apply_max_dataset_size(groups) for name, groups in groups_by_dataset.items()}
        result = {name: groups for name, groups in result.items() if groups}
        if not result:
            names = ", ".join(self._dataset_names) if self._dataset_names else "<inline>"
            raise DatasetConstraintError(f"Resolved attack-group dataset is empty (datasets: {names}).")
        return result
