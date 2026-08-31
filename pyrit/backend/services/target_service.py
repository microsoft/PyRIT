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
from functools import lru_cache
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, uuid5

from pyrit.backend.mappers.target_mappers import target_object_to_instance
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.targets import (
    CreateTargetRequest,
    TargetCatalogEntry,
    TargetCatalogResponse,
    TargetListResponse,
    TargetPersistenceStatus,
)
from pyrit.common.key_vault import parse_key_vault_secret_uri
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models.catalog.target import TargetInstance
from pyrit.models.persisted_target import PersistedTarget
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
        self._memory: MemoryInterface | None = None
        self._definition_persistence_enabled = False
        self._target_secret_key_vault_url: str | None = None

    def configure_persistence(
        self,
        *,
        memory: MemoryInterface,
        definitions_enabled: bool,
        target_secret_key_vault_url: str | None,
    ) -> None:
        """Configure persistence after Central Memory has been initialized."""
        self._memory = memory
        self._definition_persistence_enabled = definitions_enabled
        self._target_secret_key_vault_url = target_secret_key_vault_url

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

    async def list_target_catalog_async(self) -> TargetCatalogResponse:
        """
        List all available target types from the target class registry.

        Returns every constructible target with its derived constructor
        parameters and the auth modes it supports, all projected from the
        registry's ``TargetMetadata``. Deciding which entries to surface to a
        user is a presentation concern owned by the caller (e.g. the frontend),
        not this service.

        Returns:
            TargetCatalogResponse containing all available target classes.
        """
        metadata_items = await asyncio.to_thread(self._registry.get_all_registered_class_metadata)
        items: list[TargetCatalogEntry] = [
            TargetCatalogEntry(
                target_type=metadata.class_name,
                parameters=[p for p in metadata.parameters if p.is_string_coercible],
                supported_auth_modes=cast("list[Literal['api_key', 'identity']]", list(metadata.supported_auth_modes)),
                description=metadata.class_description or None,
            )
            for metadata in metadata_items
        ]
        return TargetCatalogResponse(
            items=items,
            persistence=TargetPersistenceStatus(
                definitions_enabled=self._definition_persistence_enabled,
                api_keys_enabled=bool(self._definition_persistence_enabled and self._target_secret_key_vault_url),
            ),
        )

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

        target_obj = self._registry.create_instance(request.type, **params)
        target_registry_name = target_obj.get_identifier().unique_name
        await self._persist_target_async(
            request=request,
            params=params,
            target_registry_name=target_registry_name,
        )
        self._registry.instances.register(target_obj)
        return self._build_instance_from_object(target_registry_name=target_registry_name, target_obj=target_obj)

    async def restore_persisted_targets_async(self) -> None:
        """Recreate persisted API targets and register them in original creation order."""
        if not self._definition_persistence_enabled or self._memory is None:
            return

        definitions = await asyncio.to_thread(self._memory.get_persisted_targets)
        for definition in definitions:
            try:
                params: dict[str, Any] = dict(definition.parameters)
                if definition.secret_uri:
                    params["api_key"] = await self._get_api_key_async(secret_uri=definition.secret_uri)
                if definition.auth_mode == "identity":
                    params.pop("api_key", None)
                target_obj = self._registry.create_instance(definition.target_type, **params)
                self._registry.instances.register(target_obj, name=definition.target_registry_name)
            except Exception:
                logger.exception("Failed to restore persisted target '%s'.", definition.target_registry_name)

    async def _persist_target_async(
        self,
        *,
        request: CreateTargetRequest,
        params: dict[str, Any],
        target_registry_name: str,
    ) -> None:
        """Persist a target definition when durable storage is configured."""
        if not self._definition_persistence_enabled or self._memory is None:
            return

        persisted_params = dict(params)
        api_key = persisted_params.pop("api_key", None)
        if api_key is not None and not self._target_secret_key_vault_url:
            logger.warning(
                "Target '%s' is memory-only because target_secret_key_vault_url is not configured.",
                target_registry_name,
            )
            return

        target_id = str(uuid5(NAMESPACE_URL, f"pyrit-target:{target_registry_name}"))
        secret_name = f"pyrit-target-{target_id}" if api_key is not None else None
        secret_uri = None
        if secret_name:
            secret_uri = await self._set_api_key_async(secret_name=secret_name, api_key=str(api_key))

        definition = PersistedTarget(
            id=target_id,
            target_registry_name=target_registry_name,
            target_type=request.type,
            parameters=persisted_params,
            auth_mode=request.auth_mode,
            secret_uri=secret_uri,
        )
        await asyncio.to_thread(self._memory.add_persisted_target, target=definition)

    async def _set_api_key_async(self, *, secret_name: str, api_key: str) -> str:
        """
        Store one API key in the configured Azure Key Vault.

        Returns:
            The versionless URI of the stored secret.
        """
        if not self._target_secret_key_vault_url:
            raise RuntimeError("Target secret Key Vault is not configured.")

        from azure.identity.aio import DefaultAzureCredential
        from azure.keyvault.secrets.aio import SecretClient

        async with DefaultAzureCredential() as credential:
            async with SecretClient(
                vault_url=self._target_secret_key_vault_url,
                credential=credential,
            ) as client:
                await client.set_secret(secret_name, api_key)
        return f"{self._target_secret_key_vault_url}/secrets/{secret_name}"

    async def _get_api_key_async(self, *, secret_uri: str) -> str:
        """
        Load one API key from its persisted Azure Key Vault secret URI.

        Returns:
            str: The stored API key.
        """
        from azure.identity.aio import DefaultAzureCredential
        from azure.keyvault.secrets.aio import SecretClient

        vault_url, secret_name, secret_version = parse_key_vault_secret_uri(secret_uri)
        async with DefaultAzureCredential() as credential:
            async with SecretClient(
                vault_url=vault_url,
                credential=credential,
            ) as client:
                secret = await client.get_secret(secret_name, version=secret_version)
        if secret.value is None:
            raise ValueError(f"Azure Key Vault secret '{secret_uri}' has no value.")
        return secret.value


@lru_cache(maxsize=1)
def get_target_service() -> TargetService:
    """
    Get the global target service instance.

    Returns:
        The singleton TargetService instance.
    """
    return TargetService()
