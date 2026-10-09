# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Service for scorer class discovery and registered instances."""

import asyncio
from functools import lru_cache
from typing import Any, ClassVar

from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.scorers import (
    CreateScorerRequest,
    ScorerListResponse,
    ScorerTypeEntry,
    ScorerTypeResponse,
    UpdateScorerRequest,
)
from pyrit.backend.services.instance_persistence_service import (
    BuiltInstance,
    InstanceConflictError,
    InstanceKindHandler,
    get_instance_persistence_service,
)
from pyrit.models import ComponentType
from pyrit.models.catalog.instance_recipe import InstanceRecipe
from pyrit.models.catalog.scorer import ScorerInstance
from pyrit.models.identifiers.scorer_identifier import ScorerIdentifier
from pyrit.registry import ScorerRegistry
from pyrit.registry.instance_registry import RegistryEntry


class ScorerService(InstanceKindHandler[ScorerInstance]):
    """
    Expose ScorerRegistry metadata and instances without duplicating construction logic.

    Scorers created through the API are saved by the instance persistence service,
    which calls back into this service to build and map them.
    """

    kind: ClassVar[ComponentType] = ComponentType.SCORER

    def __init__(self) -> None:
        """Initialize the service with the scorer registry singleton."""
        self._registry = ScorerRegistry.get_registry_singleton()

    def _build_instance(self, *, name: str, scorer: Any, version: str | None = None) -> ScorerInstance:
        metadata = self._registry.get_registered_class_metadata(scorer.__class__.__name__)
        return ScorerInstance(
            scorer_registry_name=name,
            identifier=ScorerIdentifier.from_component_identifier(scorer.get_identifier()),
            description=metadata.class_description or None if metadata else None,
            version=version,
        )

    def _build_instance_from_entry(self, entry: RegistryEntry[Any]) -> ScorerInstance:
        version = get_instance_persistence_service().get_version(entry)
        return self._build_instance(name=entry.name, scorer=entry.instance, version=version)

    async def list_scorer_types_async(self) -> ScorerTypeResponse:
        """
        List registered scorer class metadata without constructing scorers.

        Returns:
            ScorerTypeResponse: All registered scorer type metadata.
        """

        def list_types() -> ScorerTypeResponse:
            items = [
                ScorerTypeEntry(
                    scorer_type=metadata.class_name,
                    parameters=list(metadata.parameters),
                    is_llm_based=metadata.is_llm_based,
                    description=metadata.class_description or None,
                )
                for metadata in self._registry.get_all_registered_class_metadata()
            ]
            return ScorerTypeResponse(items=items)

        return await asyncio.to_thread(list_types)

    async def list_scorers_async(self, *, limit: int = 50, cursor: str | None = None) -> ScorerListResponse:
        """
        List named scorer instances in stable registry-name order.

        Returns:
            ScorerListResponse: A page and its pagination metadata.
        """

        def list_instances() -> ScorerListResponse:
            entries = self._registry.instances.get_all_instances()
            start = next((index + 1 for index, entry in enumerate(entries) if entry.name == cursor), 0)
            page = entries[start : start + limit]
            has_more = len(entries) > start + limit
            persistence = get_instance_persistence_service()
            return ScorerListResponse(
                items=[self._build_instance_from_entry(entry) for entry in page],
                pagination=PaginationInfo(
                    limit=limit,
                    has_more=has_more,
                    next_cursor=page[-1].name if page and has_more else None,
                    prev_cursor=cursor,
                ),
                unrestorable=persistence.get_unrestorable(self.kind),
                restore_error=persistence.restore_error,
            )

        return await asyncio.to_thread(list_instances)

    async def get_scorer_async(self, *, scorer_registry_name: str) -> ScorerInstance | None:
        """
        Get one registered scorer by name.

        Returns:
            ScorerInstance | None: The matching scorer, if present.
        """

        def get_instance() -> ScorerInstance | None:
            entry = self._registry.instances.get_entry(scorer_registry_name)
            return self._build_instance_from_entry(entry) if entry is not None else None

        return await asyncio.to_thread(get_instance)

    def describe_missing_scorer(self, *, scorer_registry_name: str) -> str:
        """
        Explain why no scorer is registered under a name.

        Returns:
            str: Why a saved scorer was not restored, or that the name was not found.
        """
        return get_instance_persistence_service().describe_missing(kind=self.kind, name=scorer_registry_name)

    async def create_scorer_async(self, *, request: CreateScorerRequest, is_admin: bool = False) -> ScorerInstance:
        """
        Build, save, and register a scorer through the shared registry resolver.

        Returns:
            ScorerInstance: The registered scorer, its identifier, and its saved version.
        """
        recipe = InstanceRecipe(
            kind=self.kind,
            name=request.name,
            type=request.type,
            params=request.params,
            credentials=request.credentials,
        )
        return await self.create_saved_async(recipe=recipe, is_admin=is_admin)

    async def update_scorer_async(
        self, *, scorer_registry_name: str, request: UpdateScorerRequest, is_admin: bool = False
    ) -> ScorerInstance:
        """
        Replace a saved scorer with a new configuration.

        Returns:
            ScorerInstance: The replacement and its new saved version.
        """
        recipe = InstanceRecipe(
            kind=self.kind,
            name=scorer_registry_name,
            type=request.type,
            params=request.params,
            credentials=request.credentials,
        )
        return await self.update_saved_async(recipe=recipe, expected_version=request.version, is_admin=is_admin)

    async def delete_scorer_async(
        self, *, scorer_registry_name: str, expected_version: str | None, is_admin: bool = False
    ) -> bool:
        """
        Delete a saved scorer and unregister the scorer built from it.

        Returns:
            bool: Whether a saved scorer was deleted.
        """
        return await self.delete_saved_async(
            name=scorer_registry_name, expected_version=expected_version, is_admin=is_admin
        )

    def normalize_recipe(self, recipe: InstanceRecipe) -> InstanceRecipe:
        """
        Check that the scorer type exists.

        Returns:
            InstanceRecipe: The recipe, unchanged.

        Raises:
            ValueError: If the scorer type is not registered.
        """
        if recipe.type not in self._registry:
            raise ValueError(f"Scorer type '{recipe.type}' not found")
        return recipe

    async def build_async(self, *, recipe: InstanceRecipe, credentials: dict[str, object]) -> BuiltInstance:
        """
        Construct a scorer without registering it.

        Returns:
            BuiltInstance: The constructed scorer.
        """
        arguments = {**recipe.params, **credentials}
        scorer = await asyncio.to_thread(lambda: self._registry.create_instance(recipe.type, **arguments))
        return BuiltInstance(instance=scorer)

    def to_response(self, *, name: str, instance: Any) -> ScorerInstance:
        """
        Map a constructed scorer to its API response.

        Returns:
            ScorerInstance: The response, without a version.
        """
        return self._build_instance(name=name, scorer=instance)

    async def release_entry_async(self, entry: RegistryEntry[Any]) -> None:
        """Scorers own nothing beyond the object, so there is nothing to free."""

    async def delete_unsaved_async(self, *, name: str) -> bool:
        """
        Refuse to delete a scorer that is not saved.

        Returns:
            bool: ``False``, because no unsaved scorer is deleted.

        Raises:
            InstanceConflictError: If an initializer registered the name.
        """
        if name in self._registry.instances:
            raise InstanceConflictError(f"Scorer '{name}' is registered by an initializer and is not saved.")
        return False


@lru_cache(maxsize=1)
def get_scorer_service() -> ScorerService:
    """Return the cached scorer service."""
    return ScorerService()
