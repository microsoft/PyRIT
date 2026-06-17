# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Buildable registry base for PyRIT.

``BuildableRegistry`` is the universal registry capability: discover classes,
introspect them into metadata, and **build** configured instances from a type
name plus a flat argument dict. Construction routes through the shared
``resolve_constructor_args`` primitive, so simple values are coerced and
registry-reference parameters (e.g. a ``PromptTarget``) are resolved by name —
the same mechanism for every domain.

Every PyRIT registry is buildable. Registries that additionally hold named
instances expose an ``instances`` property (an ``InstanceRegistry``); the
buildable layer itself only concerns the class catalog.
"""

from __future__ import annotations

from typing import TypeVar

from pyrit.registry.class_registries.base_class_registry import BaseClassRegistry
from pyrit.registry.resolution import resolve_constructor_args

T = TypeVar("T")
MetadataT = TypeVar("MetadataT")


class BuildableRegistry(BaseClassRegistry[T, MetadataT]):
    """
    Registry base that can build instances from a type name and arguments.

    Extends the class-table infrastructure of ``BaseClassRegistry`` with a
    construction path that routes through ``resolve_constructor_args``: string
    values are coerced to their annotated scalar types and registry-reference
    parameters are resolved by name from the owning domain's registry. A
    registered factory, when present, is used as-is (its arguments are not
    resolved, since a factory owns its own construction semantics).

    Type Parameters:
        T: The type of classes being registered (e.g. ``PromptConverter``).
        MetadataT: The metadata dataclass type (e.g. ``ConverterMetadata``).
    """

    def get_class_names(self) -> list[str]:
        """
        Get a sorted list of all registered class names.

        Always reflects the class catalog, even on registries that also hold
        instances (where the protocol surface ``get_names`` refers to instances on
        the ``instances`` property, not here).

        Returns:
            list[str]: The sorted class-catalog names.
        """
        self._ensure_discovered()
        return sorted(self._class_entries.keys())

    def list_class_metadata(
        self,
        *,
        include_filters: dict[str, object] | None = None,
        exclude_filters: dict[str, object] | None = None,
    ) -> list[MetadataT]:
        """
        List metadata for all registered classes, optionally filtered.

        This is the class-catalog metadata (one entry per registered class),
        distinct from any instance-level metadata a container registry exposes.
        It always reflects the class catalog, even on container registries where
        ``list_metadata`` refers to instances.

        Args:
            include_filters (dict[str, object] | None): Filters items must match.
            exclude_filters (dict[str, object] | None): Filters items must not match.

        Returns:
            list[MetadataT]: Metadata describing each registered class.
        """
        return BaseClassRegistry.list_metadata(self, include_filters=include_filters, exclude_filters=exclude_filters)

    def create_instance(self, name: str, **kwargs: object) -> T:
        """
        Build a configured instance by class name.

        Arguments are resolved via ``resolve_constructor_args`` (coerce simple
        strings, resolve registry references by name, raise on unknown params).
        When the class is registered with a factory, the factory is invoked
        directly with the given arguments instead.

        Args:
            name (str): The class-catalog name to build.
            **kwargs (object): Constructor arguments (simple values or registry
                names for reference parameters).

        Returns:
            T: The constructed instance.

        Raises:
            KeyError: If the name is not registered.
            ValueError: If an argument is not a valid constructor parameter, a
                registry reference cannot be resolved, or a value cannot be coerced.
        """
        entry = self._require_entry(name)

        if entry.factory is not None:
            return entry.create_instance(**kwargs)

        raw_args = {**entry.default_kwargs, **kwargs}
        resolved = resolve_constructor_args(cls=entry.registered_class, raw_args=raw_args)
        return entry.registered_class(**resolved)
