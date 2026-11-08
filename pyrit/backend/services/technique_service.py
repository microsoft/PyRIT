# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Thin runtime technique service; construction belongs to the registry."""

import asyncio
from functools import lru_cache

from pyrit.backend.mappers.technique_mappers import technique_to_instance
from pyrit.backend.models import PaginationInfo
from pyrit.backend.models.techniques import (
    CreateTechniqueRequest,
    TechniqueInstance,
    TechniqueListResponse,
    TechniqueTypeEntry,
    TechniqueTypeResponse,
)
from pyrit.backend.services.saved_entries import (
    SavedEntryConflictError,
    refuse_temporary_entries,
    save_then_register_async,
)
from pyrit.models import ComponentType
from pyrit.models.catalog import RegistryEntryOrigin, RegistryRecipeKind, SavedEntryOwner, TechniqueRecipe
from pyrit.registry import AttackRegistry, AttackTechniqueRegistry
from pyrit.registry.components.attack_technique_registry import TechniqueSelectorConflictError
from pyrit.registry.instance_registry import registry_entry_origin, saved_entry_owner
from pyrit.scenario.core import AttackTechniqueFactory


class TechniqueService:
    """List the active factory pool and register basic runtime configurations."""

    def __init__(self) -> None:
        """Bind to the runtime registry; service lifecycle clears this binding on reset."""
        self._registry = AttackTechniqueRegistry.get_registry_singleton()

    async def list_async(
        self, *, limit: int = 50, cursor: str | None = None, current_user_oid: str | None = None
    ) -> TechniqueListResponse:
        """
        List factories in registry-name order, including advanced programmatic definitions.

        Args:
            limit (int): The most techniques to return. Defaults to 50.
            cursor (str | None): The registry name to start after, or None for the first page. Defaults to None.
            current_user_oid (str | None): The signed-in user's Microsoft Entra object ID, used to mark the
                techniques they saved. Defaults to None.

        Returns:
            TechniqueListResponse: A page and its pagination metadata.
        """
        return await asyncio.to_thread(self._list, limit=limit, cursor=cursor, current_user_oid=current_user_oid)

    def _list(self, *, limit: int, cursor: str | None, current_user_oid: str | None) -> TechniqueListResponse:
        entries = self._registry.instances.get_all_instances()
        start = next((index + 1 for index, entry in enumerate(entries) if entry.name == cursor), 0)
        page = entries[start : start + limit]
        has_more = len(entries) > start + limit
        return TechniqueListResponse(
            items=[
                technique_to_instance(
                    name=entry.name,
                    factory=entry.instance,
                    origin=registry_entry_origin(entry.metadata),
                    owner=saved_entry_owner(entry.metadata),
                    current_user_oid=current_user_oid,
                )
                for entry in page
            ],
            pagination=PaginationInfo(
                limit=limit,
                has_more=has_more,
                next_cursor=page[-1].name if page and has_more else None,
                prev_cursor=cursor,
            ),
        )

    async def get_async(self, *, name: str, current_user_oid: str | None = None) -> TechniqueInstance | None:
        """
        Get safe settings for a named factory.

        Args:
            name (str): The technique's registry name.
            current_user_oid (str | None): The signed-in user's Microsoft Entra object ID, used to mark a technique
                they saved. Defaults to None.

        Returns:
            TechniqueInstance | None: The factory settings, if registered.
        """
        entry = self._registry.instances.get_entry(name)
        if entry is None:
            return None
        return await asyncio.to_thread(
            technique_to_instance,
            name=name,
            factory=entry.instance,
            origin=registry_entry_origin(entry.metadata),
            owner=saved_entry_owner(entry.metadata),
            current_user_oid=current_user_oid,
        )

    async def create_async(
        self,
        *,
        request: CreateTechniqueRequest,
        owner: SavedEntryOwner | None = None,
        recipes_source: str | None = None,
    ) -> TechniqueInstance:
        """
        Validate, project, and register without sending prompts or creating attacks.

        With ``persist``, the technique's recipe is saved before it's registered, so it comes back after a restart.

        Args:
            request (CreateTechniqueRequest): The technique to create.
            owner (SavedEntryOwner | None): The signed-in user, recorded on a saved technique, or None when sign-in
                is off. Defaults to None.
            recipes_source (str | None): Where saved entries are stored, or None for the default folder. Defaults to
                None.

        Returns:
            TechniqueInstance: Safe settings for the admitted factory.

        Raises:
            NotSaveableError: If a technique to save uses a Temporary converter or target.
            SavedEntryConflictError: If a technique to save has the name or a tag of a registered technique.
            SavedEntryStorageError: If the recipe can't be written, or can't be deleted after registering failed.
        """
        if request.persist:
            return await self._save_async(request=request, owner=owner, recipes_source=recipes_source)
        return await asyncio.to_thread(self._create, request)

    def _create(self, request: CreateTechniqueRequest) -> TechniqueInstance:
        factory = self._build(request)
        result = technique_to_instance(name=request.name, factory=factory, origin=RegistryEntryOrigin.TEMPORARY)
        self._registry.instances.register_runtime(factory)
        return result

    def _build(self, request: CreateTechniqueRequest) -> AttackTechniqueFactory:
        args = request.model_dump(exclude_unset=True, exclude={"name", "type", "tags", "persist"})
        return self._registry.create_factory(
            name=request.name, attack_type=request.type, technique_tags=request.tags, strict_json=True, **args
        )

    async def _save_async(
        self, *, request: CreateTechniqueRequest, owner: SavedEntryOwner | None, recipes_source: str | None
    ) -> TechniqueInstance:
        """
        Build the requested technique, save its recipe, then register it as a saved entry.

        The recipe is the request without its name, which restore builds with the same call.

        Returns:
            TechniqueInstance: The saved technique.

        Raises:
            NotSaveableError: If the technique uses a Temporary converter or target.
            SavedEntryConflictError: If the name or a tag conflicts with a registered technique.
            SavedEntryStorageError: If the recipe can't be written, or can't be deleted after registering failed.
        """
        refuse_temporary_entries(
            component_type=ComponentType.CONVERTER,
            names=[*(request.request_converters or []), *(request.response_converters or [])],
        )
        refuse_temporary_entries(component_type=ComponentType.TARGET, names=[request.adversarial_chat])
        factory = await asyncio.to_thread(self._build, request)
        try:
            await asyncio.to_thread(self._registry.instances.check_runtime_available, factory)
        except TechniqueSelectorConflictError as error:
            raise SavedEntryConflictError(str(error)) from error
        # The user saving the technique is the one asking, so the response marks it as theirs.
        result = await asyncio.to_thread(
            technique_to_instance,
            name=request.name,
            factory=factory,
            origin=RegistryEntryOrigin.SAVED,
            owner=owner,
            current_user_oid=owner.oid if owner is not None else None,
        )
        await save_then_register_async(
            kind=RegistryRecipeKind.TECHNIQUES,
            name=request.name,
            recipe=TechniqueRecipe(**request.model_dump(exclude_unset=True, exclude={"name", "persist"}), owner=owner),
            recipes_source=recipes_source,
            register=lambda metadata: self._registry.instances.register_runtime(factory, metadata=metadata),
        )
        return result

    async def types_async(self) -> TechniqueTypeResponse:
        """
        Discover metadata off the event loop.

        Returns:
            TechniqueTypeResponse: Declared construction inputs.
        """
        return await asyncio.to_thread(self._types)

    @staticmethod
    def _types() -> TechniqueTypeResponse:
        metadata = AttackRegistry.get_registry_singleton().get_all_registered_class_metadata()
        items = []
        for entry in metadata:
            names = {parameter.name for parameter in entry.parameters}
            items.append(
                TechniqueTypeEntry(
                    attack_type=entry.registry_name,
                    description=entry.class_description,
                    parameters=[
                        parameter
                        for parameter in entry.parameters
                        if parameter.name
                        not in {
                            "objective_target",
                            "attack_adversarial_config",
                            "attack_converter_config",
                            "attack_scoring_config",
                        }
                    ],
                    supports_adversarial="attack_adversarial_config" in names,
                    supports_converters="attack_converter_config" in names,
                )
            )
        return TechniqueTypeResponse(items=items)


@lru_cache(maxsize=1)
def get_technique_service() -> TechniqueService:
    """
    Get the runtime service.

    Returns:
        TechniqueService: The cached runtime service.
    """
    return TechniqueService()
