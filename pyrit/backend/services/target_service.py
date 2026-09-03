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

import asyncio
import logging
import uuid
from functools import lru_cache
from typing import Any, Literal, cast

from pyrit.backend.mappers.target_mappers import target_object_to_instance
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.targets import (
    CreateTargetRequest,
    TargetCatalogResponse,
    TargetListResponse,
    TargetTypeEntry,
    TargetTypeResponse,
)
from pyrit.models.catalog.target import TargetInstance
from pyrit.registry import TargetRegistry

logger = logging.getLogger(__name__)


class TargetService:
    """
    Service for managing target instances.

    Uses TargetRegistry as the sole source of truth for class discovery,
    parameter coercion, reference resolution, and construction. Endpoint
    validation remains owned by the target classes.
    """

    def __init__(self) -> None:
        """Initialize the target service."""
        self._registry = TargetRegistry.get_registry_singleton()

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
        cursor: str | None = None,
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
            for entry in self._registry.instances.get_all_instances()
        ]
        page, has_more = self._paginate(items=items, cursor=cursor, limit=limit)
        next_cursor = page[-1].target_registry_name if has_more and page else None
        return TargetListResponse(
            items=page,
            pagination=PaginationInfo(
                limit=limit,
                has_more=has_more,
                next_cursor=next_cursor,
                prev_cursor=cursor,
            ),
        )

    @staticmethod
    def _paginate(*, items: list[TargetInstance], cursor: str | None, limit: int) -> tuple[list[TargetInstance], bool]:
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

    async def get_target_async(self, *, target_registry_name: str) -> TargetInstance | None:
        """
        Get a target instance by registry name.

        Returns:
            TargetInstance if found, None otherwise.
        """
        obj = self._registry.instances.get(target_registry_name)
        if obj is None:
            return None
        return self._build_instance_from_object(target_registry_name=target_registry_name, target_obj=obj)

    def get_target_object(self, *, target_registry_name: str) -> Any | None:
        """
        Get the actual target object for use in attacks.

        Returns:
            The PromptTarget object if found, None otherwise.
        """
        return self._registry.instances.get(target_registry_name)

    async def list_target_types_async(self) -> TargetTypeResponse:
        """
        List all available target types from the target class registry.

        Returns every constructible target with its derived constructor
        parameters and the auth modes it supports, all projected from the
        registry's ``TargetMetadata``. Deciding which entries to surface to a
        user is a presentation concern owned by the caller (e.g. the frontend),
        not this service.

        Returns:
            TargetTypeResponse containing all available target classes.
        """
        metadata_items = await asyncio.to_thread(self._registry.get_all_registered_class_metadata)
        items: list[TargetTypeEntry] = [
            TargetTypeEntry(
                target_type=metadata.class_name,
                parameters=[p for p in metadata.parameters if p.is_string_coercible or p.reference is not None],
                supported_auth_modes=cast("list[Literal['api_key', 'identity']]", list(metadata.supported_auth_modes)),
                description=metadata.class_description or None,
            )
            for metadata in metadata_items
        ]
        return TargetTypeResponse(items=items)

    async def list_target_catalog_async(self) -> TargetCatalogResponse:
        """
        Return the legacy projection used by the current configuration UI.

        LEGACY COMPATIBILITY: ``catalog`` is the pre-registry name for ``types``, and
        the whole concept goes away -- there is no ``TargetCatalog`` class and nothing
        new should use this. It differs from ``list_target_types_async`` in exactly one
        way: it drops registry-reference parameters, which the un-migrated
        configuration UI cannot render. Delete this method, the ``/catalog`` route, and
        the ``TargetCatalog*`` aliases together when that UI switches to
        ``/targets/types``.

        Returns:
            TargetCatalogResponse: The scalar-only legacy projection.
        """
        types_response = await self.list_target_types_async()
        items = [
            entry.model_copy(update={"parameters": [p for p in entry.parameters if p.is_string_coercible]})
            for entry in types_response.items
        ]
        return TargetCatalogResponse(items=items)

    async def create_target_async(self, *, request: CreateTargetRequest) -> TargetInstance:
        """
        Create a new target instance from API request.

        Class discovery, strict parameter validation, scalar coercion, registry
        reference resolution, and construction are owned by the
        ``TargetRegistry``. Endpoint trust and identity token minting are owned
        by the target classes themselves. This service only enforces the
        request-level auth contract: for ``identity`` it confirms the target
        supports it and omits the api_key so the target validates its own
        endpoint and authenticates itself.

        Args:
            request: The create target request with type, params, and auth_mode.

        Returns:
            TargetInstance with the new target's details.

        Raises:
            ValueError: If the target type is not registered or identity auth is
                requested but unsupported by the target type. Construction errors
                (unknown params, incompatible inner targets, unrecognized identity
                endpoints) are raised by the registry / target classes.
        """
        if request.type not in self._registry:
            raise ValueError(
                f"Target type '{request.type}' not found. Available types: {self._registry.get_class_names()}"
            )

        target_cls = self._registry.get_class(request.type)
        params: dict[str, Any] = dict(request.params)

        if request.auth_mode == "identity":
            if "identity" not in target_cls.supported_auth_modes:
                raise ValueError(f"Target type '{request.type}' does not support identity-based authentication.")
            # Omit any api_key so the target validates its own endpoint and authenticates itself.
            params.pop("api_key", None)

        # LEGACY COMPATIBILITY: The current configuration UI omits the name.
        # Remove this generated fallback after that UI sends an explicit name.
        target_registry_name = request.name or f"compat_{uuid.uuid4().hex}"
        target_obj = self._registry.create_named_instance(
            name=target_registry_name,
            target_type=request.type,
            **params,
        )
        return self._build_instance_from_object(target_registry_name=target_registry_name, target_obj=target_obj)


@lru_cache(maxsize=1)
def get_target_service() -> TargetService:
    """
    Get the global target service instance.

    Returns:
        The singleton TargetService instance.
    """
    return TargetService()
