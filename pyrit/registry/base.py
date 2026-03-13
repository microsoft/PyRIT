# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared base types for PyRIT registries.

This module contains types shared between class registries (which store Type[T])
and instance registries (which store T instances).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Optional, Protocol, TypeVar, runtime_checkable

from pyrit.identifiers.class_name_utils import class_name_to_snake_case

# Type variable for metadata (invariant for Protocol compatibility)
MetadataT = TypeVar("MetadataT")


@dataclass(frozen=True)
class ClassRegistryEntry:
    """
    Minimal base for class-level registry metadata.

    Provides the common fields every registry metadata type needs for display,
    lookup, and filtering in class registries.

    Attributes:
        class_name (str): Python class name (e.g., "ContentHarmsScenario").
        class_module (str): Full module path (e.g., "pyrit.scenario.scenarios.content_harms").
        class_description (str): Human-readable description, typically from the class docstring.
    """

    class_name: str
    class_module: str
    class_description: str = ""

    @property
    def snake_class_name(self) -> str:
        """
        Snake_case version of class_name (e.g., "content_harms_scenario").

        Used by CLI formatting and as registry display keys.
        """
        return class_name_to_snake_case(self.class_name)


@runtime_checkable
class RegistryProtocol(Protocol[MetadataT]):
    """
    Protocol defining the common interface for all registries.

    Both class registries (BaseClassRegistry) and instance registries
    (BaseInstanceRegistry) implement this interface, enabling code that
    works with either registry type.

    Type Parameters:
        MetadataT: The metadata dataclass type (e.g., ScenarioMetadata).
    """

    @classmethod
    def get_registry_singleton(cls) -> "RegistryProtocol[MetadataT]":
        """Get the singleton instance of this registry."""
        ...

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance."""
        ...

    def get_names(self) -> list[str]:
        """Get a sorted list of all registered names."""
        ...

    def list_metadata(
        self,
        *,
        include_filters: Optional[dict[str, Any]] = None,
        exclude_filters: Optional[dict[str, Any]] = None,
    ) -> list[MetadataT]:
        """
        List metadata for all registered items, optionally filtered.

        Args:
            include_filters: Optional dict of filters that items must match.
                Keys are metadata property names, values are the filter criteria.
                All filters must match (AND logic).
            exclude_filters: Optional dict of filters that items must NOT match.
                Keys are metadata property names, values are the filter criteria.
                Any matching filter excludes the item.

        Returns:
            List of metadata describing each registered item.
        """
        ...

    def __contains__(self, name: str) -> bool:
        """Check if a name is registered."""
        ...

    def __len__(self) -> int:
        """Get the count of registered items."""
        ...

    def __iter__(self) -> Iterator[str]:
        """Iterate over registered names."""
        ...


def _get_metadata_value(metadata: Any, key: str) -> tuple[bool, Any]:
    """
    Get a value from a metadata object by key.

    Checks direct attributes first, then falls back to the ``params`` dict
    (used by ComponentIdentifier). Returns a (found, value) tuple.

    Args:
        metadata: The metadata object to look up.
        key (str): The attribute or params key to find.

    Returns:
        tuple: (True, value) if found, (False, None) otherwise.
    """
    if hasattr(metadata, key):
        return True, getattr(metadata, key)

    # Fall back to params dict (for ComponentIdentifier)
    params = getattr(metadata, "params", None)
    if isinstance(params, dict) and key in params:
        return True, params[key]

    return False, None


def _matches_filters(
    metadata: Any,
    *,
    include_filters: Optional[dict[str, Any]] = None,
    exclude_filters: Optional[dict[str, Any]] = None,
) -> bool:
    """
    Check if a metadata object matches all provided filters.

    Supports filtering on any property of the metadata dataclass or on keys
    inside the ``params`` dict (for ComponentIdentifier metadata):
    - For simple types (str, int, bool): exact match comparison
    - For sequence types (list, tuple): checks if filter value is contained in the sequence

    Items must match ALL include_filters (AND logic) and must NOT match ANY exclude_filters.

    Args:
        metadata: The metadata dataclass instance to check.
        include_filters: Optional dict of filters that must ALL match.
            Keys are metadata property names or params keys, values are the filter criteria.
        exclude_filters: Optional dict of filters that must ALL NOT match.
            Keys are metadata property names or params keys, values are the filter criteria.

    Returns:
        True if all include_filters match and no exclude_filters match, False otherwise.
    """
    # Check include filters - all must match
    if include_filters:
        for key, filter_value in include_filters.items():
            found, actual_value = _get_metadata_value(metadata, key)
            if not found:
                return False

            # Handle sequence types - check if filter value is in the sequence
            if isinstance(actual_value, (list, tuple)):
                if filter_value not in actual_value:
                    return False
            # Simple exact match for other types
            elif actual_value != filter_value:
                return False

    # Check exclude filters - none must match
    if exclude_filters:
        for key, filter_value in exclude_filters.items():
            found, actual_value = _get_metadata_value(metadata, key)
            if not found:
                # If the key doesn't exist, it can't match the exclude filter
                continue

            # Handle sequence types - exclude if filter value is in the sequence
            if isinstance(actual_value, (list, tuple)):
                if filter_value in actual_value:
                    return False
            # Simple exact match for other types - exclude if it matches
            elif actual_value == filter_value:
                return False

    return True
