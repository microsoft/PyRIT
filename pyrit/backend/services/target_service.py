# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Target service for managing target instances.

Handles creation, replacement, deletion, and retrieval of target instances.
Uses TargetRegistry as the source of truth for live instances.

Targets can be:
- Created via API request (built from request params, saved as a recipe, then registered)
- Restored from saved recipes when the backend starts or reinitializes
- Retrieved from registry (pre-registered at startup or created earlier)
"""

import asyncio
import logging
import uuid
from functools import lru_cache
from typing import Any, ClassVar, Literal

from pyrit.backend.mappers.target_mappers import target_object_to_instance
from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.targets import (
    CreateTargetRequest,
    TargetListResponse,
    TargetTypeEntry,
    TargetTypeResponse,
    UpdateTargetRequest,
)
from pyrit.backend.services.instance_persistence_service import (
    BuiltInstance,
    InstanceConflictError,
    InstanceKindHandler,
    get_instance_persistence_service,
)
from pyrit.common import REQUIRED_VALUE
from pyrit.models import ComponentType
from pyrit.models.catalog.instance_recipe import InstanceRecipe
from pyrit.models.catalog.target import TargetInstance
from pyrit.models.parameter import Parameter
from pyrit.registry import TargetRegistry
from pyrit.registry.instance_registry import RegistryEntry

logger = logging.getLogger(__name__)

_ENV_BACKED_REQUIRED_PARAMETERS: dict[str, frozenset[str]] = {
    "OpenAITarget": frozenset({"endpoint", "model_name"}),
    "AzureBlobStorageTarget": frozenset({"container_url"}),
    "AzureMLChatTarget": frozenset({"endpoint"}),
    "HackAPromptTarget": frozenset({"cookie", "session_id"}),
    "HuggingFaceChatTarget": frozenset({"hf_access_token"}),
    "PromptShieldTarget": frozenset({"endpoint"}),
}


class TargetService(InstanceKindHandler[TargetInstance]):
    """
    Service for managing target instances.

    Uses TargetRegistry as the sole source of truth for class discovery,
    parameter coercion, reference resolution, and construction. Endpoint
    validation remains owned by the target classes. Targets created through
    the API are saved by the instance persistence service, which calls back
    into this service to build and map them.
    """

    kind: ClassVar[ComponentType] = ComponentType.TARGET

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

    def _build_instance_from_entry(self, entry: RegistryEntry[Any]) -> TargetInstance:
        """
        Build a TargetInstance, with its saved version, from a registry entry.

        Returns:
            TargetInstance with metadata derived from the entry's object.
        """
        target = self._build_instance_from_object(target_registry_name=entry.name, target_obj=entry.instance)
        return target.model_copy(update={"version": get_instance_persistence_service().get_version(entry)})

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
            TargetListResponse containing paginated targets and the saved targets
            that could not be restored.
        """
        items = [self._build_instance_from_entry(entry) for entry in self._registry.instances.get_all_instances()]
        page, has_more = self._paginate(items=items, cursor=cursor, limit=limit)
        next_cursor = page[-1].target_registry_name if has_more and page else None
        persistence = get_instance_persistence_service()
        return TargetListResponse(
            items=page,
            pagination=PaginationInfo(
                limit=limit,
                has_more=has_more,
                next_cursor=next_cursor,
                prev_cursor=cursor,
            ),
            unrestorable=persistence.get_unrestorable(self.kind),
            restore_error=persistence.restore_error,
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
        entry = self._registry.instances.get_entry(target_registry_name)
        if entry is None:
            return None
        return self._build_instance_from_entry(entry)

    def describe_missing_target(self, *, target_registry_name: str) -> str:
        """
        Explain why no target is registered under a name.

        Returns:
            str: Why a saved target was not restored, or that the name was not found.
        """
        return get_instance_persistence_service().describe_missing(kind=self.kind, name=target_registry_name)

    def get_target_object(self, *, target_registry_name: str) -> Any | None:
        """
        Get the actual target object for use in attacks.

        Returns:
            The PromptTarget object if found, None otherwise.
        """
        return self._registry.instances.get(target_registry_name)

    @staticmethod
    def _get_supported_auth_modes(auth_modes: tuple[str, ...]) -> list[Literal["api_key", "identity"]]:
        """
        Validate and narrow registry authentication modes for the type response.

        Args:
            auth_modes (tuple[str, ...]): Authentication modes declared by a target class.

        Returns:
            list[Literal["api_key", "identity"]]: Validated authentication modes.

        Raises:
            ValueError: If a target class declares an unsupported authentication mode.
        """
        supported_auth_modes: list[Literal["api_key", "identity"]] = []
        for auth_mode in auth_modes:
            if auth_mode == "api_key" or auth_mode == "identity":
                supported_auth_modes.append(auth_mode)
                continue
            raise ValueError(f"Unsupported target authentication mode: {auth_mode!r}")
        return supported_auth_modes

    def _project_target_parameters(self, *, target_type: str, parameters: tuple[Parameter, ...]) -> list[Parameter]:
        """
        Project registry parameters into the API contract.

        Environment-backed values remain optional in Python constructors so targets
        can resolve them from dotenv configuration. The GUI must still collect them
        explicitly, so the API marks those values required without changing the
        target constructor signatures.

        Args:
            target_type (str): Registered target class name.
            parameters (tuple[Parameter, ...]): Constructor parameters derived by the registry.

        Returns:
            list[Parameter]: Parameters projected for dynamic form generation.
        """
        target_cls = self._registry.get_class(target_type)
        required_names = frozenset().union(
            *(_ENV_BACKED_REQUIRED_PARAMETERS.get(base.__name__, frozenset()) for base in target_cls.__mro__)
        )
        return [
            parameter.model_copy(update={"default": REQUIRED_VALUE}) if parameter.name in required_names else parameter
            for parameter in parameters
        ]

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
                parameters=self._project_target_parameters(
                    target_type=metadata.class_name,
                    parameters=metadata.parameters,
                ),
                supported_auth_modes=self._get_supported_auth_modes(metadata.supported_auth_modes),
                description=metadata.class_description or None,
            )
            for metadata in metadata_items
        ]
        return TargetTypeResponse(items=items)

    async def create_target_async(self, *, request: CreateTargetRequest, is_admin: bool = False) -> TargetInstance:
        """
        Create, save, and register a new target instance from an API request.

        Class discovery, strict parameter validation, scalar coercion, registry
        reference resolution, and construction are owned by the
        ``TargetRegistry``. Endpoint trust and identity token minting are owned
        by the target classes themselves. This service only enforces the
        request-level auth contract: for ``identity`` it confirms the target
        supports it, omits the api_key, and refuses a credential reference that
        would replace the identity, so the target validates its own endpoint and
        authenticates itself. The target is saved before it is registered, so a
        failed request leaves neither a saved nor a registered target.

        Args:
            request: The create target request with name, type, params, credentials, and auth_mode.
            is_admin: Whether the caller may reference server environment variables as credentials.

        Returns:
            TargetInstance with the new target's details and saved version.

        Raises:
            ValueError: If the target type is not registered, identity auth is
                requested but unsupported by the target type, or a credential is
                sent as a value. Construction errors (unknown params, incompatible
                inner targets, unrecognized identity endpoints) are raised by the
                registry / target classes.
        """
        recipe = InstanceRecipe(
            kind=self.kind,
            # LEGACY COMPATIBILITY: Older clients omit the name. Remove this generated
            # fallback when the temporary registry compatibility routes are removed.
            name=request.name or f"compat_{uuid.uuid4().hex}",
            type=request.type,
            params=request.params,
            credentials=request.credentials,
            auth_mode=request.auth_mode,
        )
        return await self.create_saved_async(recipe=recipe, is_admin=is_admin)

    async def update_target_async(
        self, *, target_registry_name: str, request: UpdateTargetRequest, is_admin: bool = False
    ) -> TargetInstance:
        """
        Replace a saved target with a new configuration.

        Returns:
            TargetInstance with the replacement's details and new saved version.
        """
        recipe = InstanceRecipe(
            kind=self.kind,
            name=target_registry_name,
            type=request.type,
            params=request.params,
            credentials=request.credentials,
            auth_mode=request.auth_mode,
        )
        return await self.update_saved_async(recipe=recipe, expected_version=request.version, is_admin=is_admin)

    async def delete_target_async(
        self, *, target_registry_name: str, expected_version: str | None, is_admin: bool = False
    ) -> bool:
        """
        Delete a saved target and unregister the target built from it.

        Returns:
            bool: Whether a saved target was deleted.
        """
        return await self.delete_saved_async(
            name=target_registry_name, expected_version=expected_version, is_admin=is_admin
        )

    def normalize_recipe(self, recipe: InstanceRecipe) -> InstanceRecipe:
        """
        Check that the target type exists and supports the requested authentication.

        Returns:
            InstanceRecipe: The recipe, without any api_key value when identity authentication is requested.

        Raises:
            ValueError: If the type is unknown, or identity authentication is unsupported or combined
                with a value or reference that the type metadata marks as replacing it.
        """
        if recipe.type not in self._registry:
            raise ValueError(
                f"Target type '{recipe.type}' not found. Available types: {self._registry.get_class_names()}"
            )
        if recipe.auth_mode != "identity":
            return recipe
        if "identity" not in self._registry.get_class(recipe.type).supported_auth_modes:
            raise ValueError(f"Target type '{recipe.type}' does not support identity-based authentication.")
        metadata = self._registry.get_registered_class_metadata(recipe.type)
        identity_conflicting = {
            parameter.name for parameter in (metadata.parameters if metadata else ()) if parameter.identity_conflicting
        }
        conflicting = sorted(
            (({"api_key"} | identity_conflicting) & recipe.credentials.keys())
            | (identity_conflicting & recipe.params.keys())
        )
        if conflicting:
            raise ValueError(
                f"Identity authentication does not use {', '.join(conflicting)}: a value or referenced credential "
                "there would replace the identity. Remove it or choose API key authentication."
            )
        # Omit any api_key so the target validates its own endpoint and authenticates itself.
        return recipe.model_copy(update={"params": {k: v for k, v in recipe.params.items() if k != "api_key"}})

    async def build_async(self, *, recipe: InstanceRecipe, credentials: dict[str, object]) -> BuiltInstance:
        """
        Construct a target without registering it.

        The constructor parameters a target derives from its authentication mode, such as
        ``AzureBlobStorageTarget``'s explicit ``auth_mode``, are applied at each build rather than saved.

        Returns:
            BuiltInstance: The constructed target.
        """
        auth_parameters = self._registry.get_class(recipe.type).get_auth_mode_parameters(
            auth_mode=recipe.auth_mode or "api_key"
        )
        return BuiltInstance(
            instance=self._registry.create_instance(recipe.type, **{**recipe.params, **credentials, **auth_parameters})
        )

    def to_response(self, *, name: str, instance: Any) -> TargetInstance:
        """
        Map a constructed target to its API response.

        Returns:
            TargetInstance: The response, without a version.
        """
        return self._build_instance_from_object(target_registry_name=name, target_obj=instance)

    async def release_entry_async(self, entry: RegistryEntry[Any]) -> None:
        """
        Leave a replaced or deleted target to whoever still uses it, as a live reinitialization does.

        An attack may still be sending through the old target, so its own cleanup, such as closing
        cached connections, is not run here.
        """

    async def delete_unsaved_async(self, *, name: str) -> bool:
        """
        Refuse to delete a target that is not saved.

        Returns:
            bool: ``False``, because no unsaved target is deleted.

        Raises:
            InstanceConflictError: If an initializer registered the name.
        """
        if name in self._registry.instances:
            raise InstanceConflictError(f"Target '{name}' is registered by an initializer and is not saved.")
        return False


@lru_cache(maxsize=1)
def get_target_service() -> TargetService:
    """
    Get the global target service instance.

    Returns:
        The singleton TargetService instance.
    """
    return TargetService()
