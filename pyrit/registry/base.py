# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Shared base types for PyRIT registries.

This module contains types shared between class registries (which store Type[T])
and instance registries (which store T instances).
"""

from typing import Any, Dict, Iterator, List, Optional, Protocol, TypeVar, runtime_checkable

# Type variable for metadata (invariant for Protocol compatibility)
MetadataT = TypeVar("MetadataT")


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

    def get_names(self) -> List[str]:
        """Get a sorted list of all registered names."""
        ...

    def list_metadata(
        self,
        *,
        include_filters: Optional[Dict[str, Any]] = None,
        exclude_filters: Optional[Dict[str, Any]] = None,
    ) -> List[MetadataT]:
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


def _matches_filters(
    metadata: Any,
    *,
    include_filters: Optional[Dict[str, Any]] = None,
    exclude_filters: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Check if a metadata object matches all provided filters.

    Supports filtering on any property of the metadata dataclass:
    - For simple types (str, int, bool): exact match comparison
    - For sequence types (list, tuple): checks if filter value is contained in the sequence

    Items must match ALL include_filters (AND logic) and must NOT match ANY exclude_filters.

    Args:
        metadata: The metadata dataclass instance to check.
        include_filters: Optional dict of filters that must ALL match.
            Keys are metadata property names, values are the filter criteria.
        exclude_filters: Optional dict of filters that must ALL NOT match.
            Keys are metadata property names, values are the filter criteria.

    Returns:
        True if all include_filters match and no exclude_filters match, False otherwise.
    """
    # Check include filters - all must match
    if include_filters:
        for key, filter_value in include_filters.items():
            if not hasattr(metadata, key):
                return False

            actual_value = getattr(metadata, key)

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
            if not hasattr(metadata, key):
                # If the key doesn't exist, it can't match the exclude filter
                continue

            actual_value = getattr(metadata, key)

            # Handle sequence types - exclude if filter value is in the sequence
            if isinstance(actual_value, (list, tuple)):
                if filter_value in actual_value:
                    return False
            # Simple exact match for other types - exclude if it matches
            elif actual_value == filter_value:
                return False

    return True
