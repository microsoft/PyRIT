# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target service for managing target instances.

Handles creation and retrieval of target instances.
Uses TargetRegistry as the source of truth for instances.

Targets can be:
- Created via API request (instantiated from request params, then registered)
- Retrieved from registry (pre-registered at startup or created earlier)
"""

from functools import lru_cache
from typing import Any, Optional

from pyrit import prompt_target
from pyrit.backend.mappers.target_mappers import target_object_to_instance
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.targets import (
    CreateTargetRequest,
    TargetInstance,
    TargetListResponse,
)
from pyrit.prompt_target import PromptTarget
from pyrit.registry.object_registries import TargetRegistry


def _build_target_class_registry() -> dict[str, type]:
    """
    Build a registry mapping target class names to their classes.

    Uses the prompt_target module's __all__ to discover all available targets.

    Returns:
        Dict mapping class name (str) to class (type).
    """
    registry: dict[str, type] = {}
    for name in prompt_target.__all__:
        cls = getattr(prompt_target, name, None)
        if cls is not None and isinstance(cls, type) and issubclass(cls, PromptTarget):
            registry[name] = cls
    return registry


# Module-level class registry (built once on import)
_TARGET_CLASS_REGISTRY: dict[str, type] = _build_target_class_registry()


class TargetService:
    """
    Service for managing target instances.

    Uses TargetRegistry as the sole source of truth.
    API metadata is derived from the target objects' identifiers.
    """

    def __init__(self) -> None:
        """Initialize the target service."""
        self._registry = TargetRegistry.get_registry_singleton()

    def _get_target_class(self, *, target_type: str) -> type:
        """
        Get the target class for a given type name.

        Looks up the class in the module-level target class registry.

        Args:
            target_type: The exact class name of the target (e.g., 'TextTarget').

        Returns:
            The target class.

        Raises:
            ValueError: If the target type is not found.
        """
        cls = _TARGET_CLASS_REGISTRY.get(target_type)
        if cls is None:
            raise ValueError(
                f"Target type '{target_type}' not found. Available types: {sorted(_TARGET_CLASS_REGISTRY.keys())}"
            )
        return cls

    def _build_instance_from_object(self, *, target_registry_name: str, target_obj: Any) -> TargetInstance:
        """
        Build a TargetInstance from a registry object.

        Returns:
            TargetInstance with metadata derived from the object.
        """
        return target_object_to_instance(target_registry_name, target_obj)

    async def list_targets_async(
        self,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> TargetListResponse:
        """
        List all target instances with pagination.

        Args:
            limit: Maximum items to return.
            cursor: Pagination cursor (target_registry_name to start after).

        Returns:
            TargetListResponse containing paginated targets.
        """
        items = [
            self._build_instance_from_object(target_registry_name=entry.name, target_obj=entry.instance)
            for entry in self._registry.get_all_instances()
        ]
        page, has_more = self._paginate(items, cursor, limit)
        next_cursor = page[-1].target_registry_name if has_more and page else None
        return TargetListResponse(
            items=page,
            pagination=PaginationInfo(limit=limit, has_more=has_more, next_cursor=next_cursor, prev_cursor=cursor),
        )

    @staticmethod
    def _paginate(items: list[TargetInstance], cursor: Optional[str], limit: int) -> tuple[list[TargetInstance], bool]:
        """
        Apply cursor-based pagination.

        Returns:
            Tuple of (paginated items, has_more flag).
        """
        start_idx = 0
        if cursor:
            for i, item in enumerate(items):
                if item.target_registry_name == cursor:
                    start_idx = i + 1
                    break

        page = items[start_idx : start_idx + limit]
        has_more = len(items) > start_idx + limit
        return page, has_more

    async def get_target_async(self, *, target_registry_name: str) -> Optional[TargetInstance]:
        """
        Get a target instance by registry name.

        Returns:
            TargetInstance if found, None otherwise.
        """
        obj = self._registry.get_instance_by_name(target_registry_name)
        if obj is None:
            return None
        return self._build_instance_from_object(target_registry_name=target_registry_name, target_obj=obj)

    def get_target_object(self, *, target_registry_name: str) -> Optional[Any]:
        """
        Get the actual target object for use in attacks.

        Returns:
            The PromptTarget object if found, None otherwise.
        """
        return self._registry.get_instance_by_name(target_registry_name)

    async def create_target_async(self, *, request: CreateTargetRequest) -> TargetInstance:
        """
        Create a new target instance from API request.

        Instantiates the target with the given type and params,
        then registers it in the registry under its registry name.

        Args:
            request: The create target request with type and params.

        Returns:
            TargetInstance with the new target's details.

        Raises:
            ValueError: If the target type is not found.
        """
        # Instantiate from request params and register (uses unique_name as key by default)
        target_class = self._get_target_class(target_type=request.type)
        target_obj = target_class(**request.params)
        self._registry.register_instance(target_obj)

        # Build response from the registered instance
        target_registry_name = target_obj.get_identifier().unique_name
        return self._build_instance_from_object(target_registry_name=target_registry_name, target_obj=target_obj)


@lru_cache(maxsize=1)
def get_target_service() -> TargetService:
    """
    Get the global target service instance.

    Returns:
        The singleton TargetService instance.
    """
    return TargetService()
