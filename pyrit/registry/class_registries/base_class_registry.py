# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Base class registry for PyRIT.

This module provides the abstract base class for registries that store classes (Type[T]).
These registries allow on-demand instantiation of registered classes.

For registries that store pre-configured instances, see instance_registries/.

Terminology:
- **Metadata**: A TypedDict describing a registered class (e.g., ScenarioMetadata)
- **Class**: The actual Python class (Type[T]) that can be instantiated
- **Instance**: A created object of that class
- **ClassEntry**: Internal wrapper holding a class plus optional factory/defaults
"""

from abc import ABC, abstractmethod
from typing import Callable, Dict, Generic, Iterator, List, Optional, Type, TypeVar

from pyrit.identifiers import Identifier
from pyrit.identifiers.class_name_utils import class_name_to_snake_case
from pyrit.registry.base import RegistryProtocol

# Type variable for the registered class type
T = TypeVar("T")
# Type variable for the metadata TypedDict
MetadataT = TypeVar("MetadataT")


class ClassEntry(Generic[T]):
    """
    Internal wrapper for a registered class.

    This holds the class itself (Type[T]) along with optional factory
    and default parameters for creating instances.

    Note: This is an internal implementation detail. Users interact with
    registries via get_class(), create_instance(), and list_metadata().

    Attributes:
        registered_class: The actual Python class (Type[T]).
        factory: Optional callable to create instances with custom logic.
        default_kwargs: Default keyword arguments for instance creation.
        description: Optional description override.
    """

    def __init__(
        self,
        *,
        registered_class: Type[T],
        factory: Optional[Callable[..., T]] = None,
        default_kwargs: Optional[Dict[str, object]] = None,
        description: Optional[str] = None,
    ) -> None:
        """
        Initialize a class entry.

        Args:
            registered_class: The actual Python class (Type[T]).
            factory: Optional callable that creates an instance.
            default_kwargs: Default keyword arguments for instantiation.
            description: Optional description override.
        """
        self.registered_class = registered_class
        self.factory = factory
        self.default_kwargs = default_kwargs or {}
        self.description = description

    def create_instance(self, **kwargs: object) -> T:
        """
        Create an instance of the registered class.

        Args:
            **kwargs: Additional keyword arguments. These override default_kwargs.

        Returns:
            An instance of type T.
        """
        merged_kwargs = {**self.default_kwargs, **kwargs}

        if self.factory is not None:
            return self.factory(**merged_kwargs)
        else:
            return self.registered_class(**merged_kwargs)


class BaseClassRegistry(ABC, RegistryProtocol[MetadataT], Generic[T, MetadataT]):
    """
    Abstract base class for registries that store classes (Type[T]).

    This class implements RegistryProtocol and provides the common infrastructure
    for class registries including:
    - Lazy discovery of classes
    - Registration of classes or factory callables
    - Metadata caching
    - Consistent API: get_class(), get_names(), list_metadata(), create_instance()
    - Singleton pattern support via get_registry_singleton()

    Subclasses must implement:
    - _discover(): Populate the registry with discovered classes
    - _build_metadata(): Build a metadata TypedDict for a class

    Type Parameters:
        T: The type of classes being registered (e.g., Scenario, PromptTarget).
        MetadataT: The TypedDict type for metadata (e.g., ScenarioMetadata).
    """

    # Class-level singleton instances, keyed by registry class
    _instances: Dict[type, "BaseClassRegistry[object, object]"] = {}

    def __init__(self, *, lazy_discovery: bool = True) -> None:
        """
        Initialize the registry.

        Args:
            lazy_discovery: If True, discovery is deferred until first access.
                If False, discovery runs immediately in constructor.
        """
        # Maps registry names to ClassEntry wrappers
        self._class_entries: Dict[str, ClassEntry[T]] = {}
        self._metadata_cache: Optional[List[MetadataT]] = None
        self._discovered = False
        self._lazy_discovery = lazy_discovery

        if not lazy_discovery:
            self._discover()
            self._discovered = True

    @classmethod
    def get_registry_singleton(cls) -> "BaseClassRegistry[T, MetadataT]":
        """
        Get the singleton instance of this registry.

        Creates the instance on first call with default parameters.

        Returns:
            The singleton instance of this registry class.
        """
        if cls not in cls._instances:
            cls._instances[cls] = cls()  # type: ignore[assignment]
        return cls._instances[cls]  # type: ignore[return-value]

    @classmethod
    def reset_instance(cls) -> None:
        """
        Reset the singleton instance.

        Useful for testing or when re-discovery is needed.
        """
        if cls in cls._instances:
            del cls._instances[cls]

    def _ensure_discovered(self) -> None:
        """Ensure discovery has been performed. Runs discovery on first access."""
        if not self._discovered:
            self._discover()
            self._discovered = True

    @abstractmethod
    def _discover(self) -> None:
        """
        Perform discovery of registry classes.

        Subclasses implement this to populate self._class_entries with discovered classes.
        """
        pass

    @abstractmethod
    def _build_metadata(self, name: str, entry: ClassEntry[T]) -> MetadataT:
        """
        Build metadata dictionary for a registered class.

        Subclasses must implement this to provide registry-specific metadata.

        Args:
            name: The registry name (snake_case identifier).
            entry: The ClassEntry containing the registered class.

        Returns:
            A metadata dataclass with descriptive information about the registered class.
        """
        pass

    def _build_base_metadata(self, name: str, entry: ClassEntry[T]) -> Identifier:
        """
        Build the common base metadata for a registered class.

        This helper extracts fields common to all registries: name, class_name, class_description.
        Subclasses can use this for building common fields if needed.

        Args:
            name: The registry name (snake_case identifier).
            entry: The ClassEntry containing the registered class.

        Returns:
            An Identifier dataclass with common fields.
        """
        registered_class = entry.registered_class

        # Extract description from docstring, clean up whitespace
        doc = registered_class.__doc__ or ""
        if doc:
            description = " ".join(doc.split())
        else:
            description = entry.description or "No description available"

        return Identifier(
            identifier_type="class",
            class_name=registered_class.__name__,
            class_module=registered_class.__module__,
            class_description=description,
        )

    def get_class(self, name: str) -> Type[T]:
        """
        Get a registered class by name.

        Args:
            name: The registry name (snake_case identifier).

        Returns:
            The registered class (Type[T]).
            Note: This returns the class itself, not an instance.

        Raises:
            KeyError: If the name is not registered.
        """
        self._ensure_discovered()
        entry = self._class_entries.get(name)
        if entry is None:
            available = ", ".join(self.get_names())
            raise KeyError(f"'{name}' not found in registry. Available: {available}")
        return entry.registered_class

    def get_entry(self, name: str) -> Optional[ClassEntry[T]]:
        """
        Get the full ClassEntry for a registered class.

        This is useful when you need access to factory or default_kwargs.

        Args:
            name: The registry name.

        Returns:
            The ClassEntry containing class, factory, and defaults, or None if not found.
        """
        self._ensure_discovered()
        return self._class_entries.get(name)

    def get_names(self) -> List[str]:
        """
        Get a sorted list of all registered names.

        These are the snake_case registry keys (e.g., "encoding", "self_ask_refusal"),
        not the actual class names (e.g., "EncodingScenario", "SelfAskRefusalScorer").

        Returns:
            Sorted list of registry names.
        """
        self._ensure_discovered()
        return sorted(self._class_entries.keys())

    def list_metadata(
        self,
        *,
        include_filters: Optional[Dict[str, object]] = None,
        exclude_filters: Optional[Dict[str, object]] = None,
    ) -> List[MetadataT]:
        """
        List metadata for all registered classes, optionally filtered.

        Supports filtering on any metadata property:
        - Simple types (str, int, bool): exact match
        - List types: checks if filter value is in the list

        Args:
            include_filters: Optional dict of filters that items must match.
                Keys are metadata property names, values are the filter criteria.
                All filters must match (AND logic).
            exclude_filters: Optional dict of filters that items must NOT match.
                Keys are metadata property names, values are the filter criteria.
                Any matching filter excludes the item.

        Returns:
            List of metadata dictionaries (TypedDict) describing each registered class.
            Note: This returns descriptive info, not the classes themselves.
        """
        from pyrit.registry.base import _matches_filters

        self._ensure_discovered()

        if self._metadata_cache is None:
            self._metadata_cache = [
                self._build_metadata(name, entry) for name, entry in sorted(self._class_entries.items())
            ]

        if not include_filters and not exclude_filters:
            return self._metadata_cache

        return [
            m
            for m in self._metadata_cache
            if _matches_filters(m, include_filters=include_filters, exclude_filters=exclude_filters)
        ]

    def register(
        self,
        cls: Type[T],
        *,
        name: Optional[str] = None,
        factory: Optional[Callable[..., T]] = None,
        default_kwargs: Optional[Dict[str, object]] = None,
        description: Optional[str] = None,
    ) -> None:
        """
        Register a class with the registry.

        Args:
            cls: The class to register (Type[T], not an instance).
            name: Optional custom registry name. If not provided, derived from class name.
            factory: Optional callable for creating instances with custom logic.
            default_kwargs: Default keyword arguments for instance creation.
            description: Optional description override.
        """
        if name is None:
            name = self._get_registry_name(cls)

        entry = ClassEntry(
            registered_class=cls,
            factory=factory,
            default_kwargs=default_kwargs,
            description=description,
        )
        self._class_entries[name] = entry
        self._metadata_cache = None

    def create_instance(self, name: str, **kwargs: object) -> T:
        """
        Create an instance of a registered class.

        Args:
            name: The registry name of the class.
            **kwargs: Keyword arguments to pass to the factory or constructor.

        Returns:
            A new instance of type T.

        Raises:
            KeyError: If the name is not registered.
        """
        self._ensure_discovered()
        entry = self._class_entries.get(name)
        if entry is None:
            available = ", ".join(self.get_names())
            raise KeyError(f"'{name}' not found in registry. Available: {available}")
        return entry.create_instance(**kwargs)

    def _get_registry_name(self, cls: Type[T]) -> str:
        """
        Get the registry name for a class.

        Subclasses can override this to customize name derivation.
        Default implementation converts CamelCase to snake_case.

        Args:
            cls: The class to get a name for.

        Returns:
            The registry name (snake_case identifier).
        """
        return class_name_to_snake_case(cls.__name__)

    def __contains__(self, name: str) -> bool:
        """
        Check if a name is registered.

        Returns:
            True if the name is registered, False otherwise.
        """
        self._ensure_discovered()
        return name in self._class_entries

    def __len__(self) -> int:
        """
        Get the count of registered classes.

        Returns:
            The number of registered classes.
        """
        self._ensure_discovered()
        return len(self._class_entries)

    def __iter__(self) -> Iterator[str]:
        """
        Iterate over registered names.

        Returns:
            An iterator over sorted registered names.
        """
        self._ensure_discovered()
        return iter(sorted(self._class_entries.keys()))
