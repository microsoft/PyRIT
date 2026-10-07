# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded cache keys for data derived from the runtime factory pool."""

from collections.abc import Callable
from functools import lru_cache, update_wrapper
from typing import Generic, TypeVar

from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry

T = TypeVar("T")


class CatalogBuilder(Generic[T]):
    """A revision-aware builder with the existing cache-reset interface."""

    def __init__(self, builder: Callable[[], T]) -> None:
        """Retain one snapshot and preserve the builder's public metadata."""

        @lru_cache(maxsize=1)
        def cached(revision: tuple[object, int]) -> T:
            return builder()

        self._cached = cached
        update_wrapper(self, builder)

    def __call__(self) -> T:
        """
        Build or return the current catalog snapshot.

        Returns:
            T: The current revision's snapshot.
        """
        return self._cached(technique_catalog_revision())

    def cache_clear(self) -> None:
        """Discard the retained snapshot."""
        self._cached.cache_clear()


def technique_catalog_revision() -> tuple[object, int]:
    """Return a key that changes on mutation or replacement of the factory registry."""
    return AttackTechniqueRegistry.get_registry_singleton().catalog_revision


def technique_catalog_cache(builder: Callable[[], T]) -> CatalogBuilder[T]:
    """
    Cache one builder result for the current factory pool, retaining old snapshots.

    Returns:
        CatalogBuilder[T]: A bounded revision-aware builder.
    """
    return CatalogBuilder(builder)
