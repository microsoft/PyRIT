# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Registry base for PyRIT.

``Registry`` is the universal registry capability: discover classes, introspect
them into metadata, and construct configured instances from a type name plus a
flat argument dict. Construction routes through the shared
``resolve_constructor_args`` primitive, so simple values are coerced and
registry-reference parameters (e.g. a ``PromptTarget``) are resolved by name —
the same mechanism for every domain.

It owns a single add path: ``_discover()`` populates the catalog by calling
``register_class()``, which validates the class (its build contract must be
derivable and every reference parameter must map to a wired registry) before it
is stored. Validation therefore happens once, at registration time; there is no
separate post-hoc sweep.

Every PyRIT registry is a ``Registry``. Registries that additionally hold named,
pre-built component objects expose an ``instances`` property (an
``InstanceRegistry``); the class catalog itself only concerns classes. After this
layering, "instance" only ever means a built component object — never the
registry singleton.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Generic, TypeVar

from pyrit.models import class_name_to_snake_case
from pyrit.registry.resolution import (
    derive_parameters,
    is_component_type_resolvable,
    resolve_constructor_args,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from typing_extensions import Self

    from pyrit.models.identifiers.component_identifier import ComponentIdentifier

T = TypeVar("T")
MetadataT = TypeVar("MetadataT")


class Registry(ABC, Generic[T, MetadataT]):
    """
    Standalone base for PyRIT registries: a validated class catalog that builds instances.

    Provides the common infrastructure every registry needs:

    - Lazy discovery of classes (deferred until first access).
    - A single add path (``register_class``) that validates a class before storing it.
    - Metadata caching keyed by registry name.
    - Construction from a type name plus arguments (``create_instance``), routed
      through ``resolve_constructor_args`` so string values are coerced and
      registry-reference parameters are resolved by name from the owning domain.
    - Singleton support via ``get_registry_singleton()``.

    Subclasses must implement:

    - ``_discover()`` — populate the catalog by calling ``register_class`` for each class.
    - ``_build_metadata(name, cls)`` — build the metadata descriptor for a class.

    Type Parameters:
        T: The type of classes being registered (e.g. ``PromptConverter``).
        MetadataT: The metadata dataclass type (e.g. ``ConverterMetadata``).
    """

    # Class-level singleton instances, keyed by registry class.
    _singletons: dict[type, Registry[object, object]] = {}

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        """
        Initialize the registry.

        Args:
            lazy_discovery (bool): If True, discovery is deferred until first access.
                If False, discovery runs immediately in the constructor.
        """
        self._classes: dict[str, type[T]] = {}
        self._metadata_cache: dict[str, MetadataT] | None = None
        self._discovered = False
        self._lazy_discovery = lazy_discovery

        if not lazy_discovery:
            self._discover()
            self._discovered = True

    @classmethod
    def get_registry_singleton(cls) -> Self:
        """
        Get the singleton instance of this registry.

        Creates the instance on first call with default parameters.

        Returns:
            The singleton instance of this registry class.
        """
        if cls not in cls._singletons:
            cls._singletons[cls] = cls()  # type: ignore[ty:invalid-assignment]
        return cls._singletons[cls]  # type: ignore[ty:invalid-return-type]

    @classmethod
    def reset_registry_singleton(cls) -> None:
        """
        Reset the singleton instance.

        Useful for testing or when re-discovery is needed.
        """
        if cls in cls._singletons:
            del cls._singletons[cls]

    def _ensure_discovered(self) -> None:
        """Ensure discovery has been performed. Runs discovery on first access."""
        if not self._discovered:
            self._discover()
            self._discovered = True

    @abstractmethod
    def _discover(self) -> None:
        """
        Perform discovery of registry classes.

        Subclasses implement this to populate the catalog by calling
        ``self.register_class(cls)`` for each discovered class.
        """

    @abstractmethod
    def _build_metadata(self, name: str, cls: type[T]) -> MetadataT:
        """
        Build the metadata descriptor for a registered class.

        Subclasses must implement this to provide registry-specific metadata.

        Args:
            name (str): The catalog name (the registry key) for the class.
            cls (type[T]): The registered class to describe.

        Returns:
            MetadataT: A metadata descriptor for the registered class.
        """

    def _identifier_type(self) -> type[ComponentIdentifier] | None:
        """
        Return the domain identifier whose ``Param.*`` markers drive derivation.

        The base registry has no domain identifier, so no constructor parameter is
        treated as a registry reference. Domain registries (e.g.
        ``ConverterRegistry``) override this to return their identifier type so that
        ``Param.Exclude`` / ``Param.Include`` markers are honored.

        Returns:
            type[ComponentIdentifier] | None: The domain identifier type, or None.
        """
        return None

    def _get_registry_name(self, cls: type[T]) -> str:
        """
        Get the catalog name for a class.

        Subclasses can override this to customize name derivation. The default
        converts CamelCase to snake_case.

        Args:
            cls (type[T]): The class to get a name for.

        Returns:
            str: The catalog name (snake_case identifier by default).
        """
        return class_name_to_snake_case(cls.__name__)

    def _validate_class(self, cls: type[T]) -> None:
        """
        Verify the registry can describe and build a class.

        Derives the class's ``Parameter`` contract (raising if its constructor
        cannot be introspected) and checks that every reference parameter maps to a
        registry the resolver knows how to query. This is the registration gate: a
        class whose build contract does not line up with a resolvable reference
        fails fast at ``register_class`` time instead of erroring only at build time.

        Args:
            cls (type[T]): The class to validate.

        Raises:
            ValueError: If the constructor cannot be introspected or a reference
                parameter has no registry wired for its component type.
        """
        parameters = derive_parameters(cls=cls, identifier_type=self._identifier_type())
        for param in parameters:
            if param.reference is not None and not is_component_type_resolvable(param.reference.component_type):
                raise ValueError(
                    f"{cls.__name__}: reference parameter '{param.name}' has no registry wired for component type "
                    f"'{param.reference.component_type}'."
                )

    def register_class(self, cls: type[T], *, name: str | None = None) -> None:
        """
        Add a class to the catalog after validating it.

        Registers a class *type* (not an instance) so the registry knows it exists
        and can later build instances of it via ``create_instance``. The class is
        validated by ``_validate_class`` before being stored, so the catalog never
        holds a class whose build contract cannot be resolved.

        Args:
            cls (type[T]): The class to register.
            name (str | None): Optional custom catalog name. If not provided, it is
                derived via ``_get_registry_name``.

        Raises:
            ValueError: If the class fails validation.
        """
        if name is None:
            name = self._get_registry_name(cls)
        self._validate_class(cls)
        self._classes[name] = cls
        self._metadata_cache = None

    def get_class(self, name: str) -> type[T]:
        """
        Get a registered class by name.

        Args:
            name (str): The catalog name.

        Returns:
            type[T]: The registered class (the class itself, not an instance).

        Raises:
            KeyError: If the name is not registered.
        """
        self._ensure_discovered()
        cls = self._classes.get(name)
        if cls is None:
            available = ", ".join(self.get_class_names())
            raise KeyError(f"'{name}' not found in registry. Available: {available}")
        return cls

    def get_class_names(self) -> list[str]:
        """
        Get a sorted list of all registered catalog names.

        Returns:
            list[str]: Sorted catalog names.
        """
        self._ensure_discovered()
        return sorted(self._classes.keys())

    def _ensure_metadata(self) -> dict[str, MetadataT]:
        """
        Build (once) and return the metadata cache keyed by catalog name.

        Returns:
            dict[str, MetadataT]: Metadata for every registered class, keyed by name.
        """
        self._ensure_discovered()
        if self._metadata_cache is None:
            self._metadata_cache = {
                name: self._build_metadata(name, cls) for name, cls in sorted(self._classes.items())
            }
        return self._metadata_cache

    def get_all_registered_class_metadata(
        self,
        *,
        include_filters: dict[str, object] | None = None,
        exclude_filters: dict[str, object] | None = None,
    ) -> list[MetadataT]:
        """
        List metadata for all registered classes, optionally filtered.

        Supports filtering on any metadata property:

        - Simple types (str, int, bool): exact match.
        - Sequence types (list, tuple): checks if the filter value is contained.

        Args:
            include_filters (dict[str, object] | None): Filters that items must match
                (AND logic). Keys are metadata property names.
            exclude_filters (dict[str, object] | None): Filters that exclude an item
                when matched. Keys are metadata property names.

        Returns:
            list[MetadataT]: Metadata describing each registered class (filtered).
        """
        from pyrit.registry.base import _matches_filters

        metadata = list(self._ensure_metadata().values())
        if not include_filters and not exclude_filters:
            return metadata

        return [
            m for m in metadata if _matches_filters(m, include_filters=include_filters, exclude_filters=exclude_filters)
        ]

    def get_registered_class_metadata(self, name: str) -> MetadataT | None:
        """
        Get the metadata for a single registered class by name.

        Args:
            name (str): The catalog name.

        Returns:
            MetadataT | None: The metadata, or None if the name is not registered.
        """
        return self._ensure_metadata().get(name)

    def get_class_metadata(self, cls: type[T]) -> MetadataT:
        """
        Build metadata for any class (registered or not).

        Derives the catalog name via ``_get_registry_name`` and builds a fresh
        descriptor. Useful for describing a class without registering it.

        Args:
            cls (type[T]): The class to describe.

        Returns:
            MetadataT: The metadata descriptor for the class.
        """
        return self._build_metadata(self._get_registry_name(cls), cls)

    def create_instance(self, name: str, **kwargs: object) -> T:
        """
        Build a configured instance by class name.

        Looks up the catalogued class, resolves the given arguments via
        ``resolve_constructor_args`` (coerce simple strings, resolve registry
        references by name, raise on unknown params), and constructs the object.

        Args:
            name (str): The catalog name to build.
            **kwargs (object): Constructor arguments (simple values or registry
                names for reference parameters).

        Returns:
            T: The constructed instance.

        Raises:
            KeyError: If the name is not registered.
            ValueError: If an argument is not a valid constructor parameter, a
                registry reference cannot be resolved, or a value cannot be coerced.
        """
        cls = self.get_class(name)
        resolved = resolve_constructor_args(
            cls=cls,
            raw_args=dict(kwargs),
            identifier_type=self._identifier_type(),
        )
        return cls(**resolved)

    def __contains__(self, name: str) -> bool:
        """
        Check if a name is registered.

        Returns:
            bool: True if the name is registered, False otherwise.
        """
        self._ensure_discovered()
        return name in self._classes

    def __len__(self) -> int:
        """
        Get the count of registered classes.

        Returns:
            int: The number of registered classes.
        """
        self._ensure_discovered()
        return len(self._classes)

    def __iter__(self) -> Iterator[str]:
        """
        Iterate over registered names.

        Returns:
            Iterator[str]: An iterator over sorted registered names.
        """
        self._ensure_discovered()
        return iter(sorted(self._classes.keys()))
