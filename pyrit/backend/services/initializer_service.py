# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Initializer service for listing available initializers.

Provides read-only access to the InitializerRegistry, exposing initializer
metadata through the REST API.
"""

from functools import lru_cache

from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.initializers import (
    InitializerParameterSummary,
    ListRegisteredInitializersResponse,
    RegisteredInitializer,
)
from pyrit.registry import InitializerMetadata, InitializerRegistry


def _metadata_to_registered_initializer(metadata: InitializerMetadata) -> RegisteredInitializer:
    """
    Convert an InitializerMetadata dataclass to a RegisteredInitializer Pydantic model.

    Args:
        metadata: The registry metadata for an initializer.

    Returns:
        RegisteredInitializer Pydantic model.
    """
    return RegisteredInitializer(
        initializer_name=metadata.registry_name,
        initializer_type=metadata.class_name,
        description=metadata.class_description,
        required_env_vars=list(metadata.required_env_vars),
        supported_parameters=[
            InitializerParameterSummary(
                name=name,
                description=desc,
                default=default,
            )
            for name, desc, default in metadata.supported_parameters
        ],
    )


class InitializerService:
    """
    Service for listing available initializers.

    Uses InitializerRegistry as the source of truth for initializer metadata.
    """

    def __init__(self) -> None:
        """Initialize the initializer service."""
        self._registry = InitializerRegistry.get_registry_singleton()

    async def list_initializers_async(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ListRegisteredInitializersResponse:
        """
        List all available initializers with pagination.

        Args:
            limit: Maximum items to return per page.
            cursor: Pagination cursor (initializer_name to start after).

        Returns:
            ListRegisteredInitializersResponse with paginated initializer summaries.
        """
        all_metadata = self._registry.list_metadata()
        all_summaries = [_metadata_to_registered_initializer(m) for m in all_metadata]

        page, has_more = self._paginate(items=all_summaries, cursor=cursor, limit=limit)
        next_cursor = page[-1].initializer_name if has_more and page else None

        return ListRegisteredInitializersResponse(
            items=page,
            pagination=PaginationInfo(limit=limit, has_more=has_more, next_cursor=next_cursor, prev_cursor=cursor),
        )

    async def get_initializer_async(self, *, initializer_name: str) -> RegisteredInitializer | None:
        """
        Get a single initializer by registry name.

        Args:
            initializer_name: The registry key of the initializer (e.g., 'target').

        Returns:
            RegisteredInitializer if found, None otherwise.
        """
        all_metadata = self._registry.list_metadata()
        for metadata in all_metadata:
            if metadata.registry_name == initializer_name:
                return _metadata_to_registered_initializer(metadata)
        return None

    @staticmethod
    def _paginate(
        *,
        items: list[RegisteredInitializer],
        cursor: str | None,
        limit: int,
    ) -> tuple[list[RegisteredInitializer], bool]:
        """
        Apply cursor-based pagination.

        Args:
            items: Full list of items.
            cursor: Initializer name to start after.
            limit: Maximum items per page.

        Returns:
            Tuple of (paginated items, has_more flag).
        """
        start_idx = 0
        if cursor:
            for i, item in enumerate(items):
                if item.initializer_name == cursor:
                    start_idx = i + 1
                    break

        page = items[start_idx : start_idx + limit]
        has_more = len(items) > start_idx + limit
        return page, has_more


@lru_cache(maxsize=1)
def get_initializer_service() -> InitializerService:
    """
    Get the global initializer service instance.

    Returns:
        The singleton InitializerService instance.
    """
    return InitializerService()
