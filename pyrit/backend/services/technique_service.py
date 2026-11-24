# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Thin runtime technique service; construction belongs to the registry."""

import asyncio
from functools import lru_cache

from pyrit.backend.mappers.technique_mappers import technique_to_instance
from pyrit.backend.models import PaginationInfo
from pyrit.backend.models.common import SaveabilityResponse
from pyrit.backend.models.techniques import (
    CreateTechniqueRequest,
    TechniqueInstance,
    TechniqueListResponse,
    TechniqueSaveabilityRequest,
    TechniqueTypeEntry,
    TechniqueTypeResponse,
)
from pyrit.models.catalog import (
    RegistryEntryOrigin,
    RegistryRecipeKind,
    SavedEntryOwner,
    TechniqueRecipe,
)
from pyrit.registry import (
    AttackRegistry,
    AttackTechniqueRegistry,
    NotSaveableError,
    SavedEntryConflictError,
    check_saved_technique_async,
    creator_metadata,
    record_private_references,
    save_then_register_async,
)
from pyrit.registry.components.attack_technique_registry import TechniqueSelectorConflictError
from pyrit.registry.instance_registry import registry_entry_origin
from pyrit.scenario.core import AttackTechniqueFactory


class TechniqueService:
    """List the active factory pool and register basic runtime configurations."""

    def __init__(self) -> None:
        """Bind to the runtime registry; service lifecycle clears this binding on reset."""
        self._registry = AttackTechniqueRegistry.get_registry_singleton()

    async def list_async(self, *, limit: int = 50, cursor: str | None = None) -> TechniqueListResponse:
        """
        List factories in registry-name order, including advanced programmatic definitions.

        Returns:
            TechniqueListResponse: A page and its pagination metadata.

        Raises:
            ValueError: If the cursor does not name a registered technique.
        """
        return await asyncio.to_thread(self._list, limit=limit, cursor=cursor)

    def _list(self, *, limit: int, cursor: str | None) -> TechniqueListResponse:
        entries = self._registry.instances.get_all_instances()
        start = next((index + 1 for index, entry in enumerate(entries) if entry.name == cursor), None)
        if cursor and start is None:
            # A cursor naming an entry that no longer exists must not silently
            # restart at the first page, which would serve duplicate rows.
            raise ValueError("The cursor is not valid for the technique registry")
        start = start or 0
        page = entries[start : start + limit]
        has_more = len(entries) > start + limit
        return TechniqueListResponse(
            items=[
                technique_to_instance(
                    name=entry.name, factory=entry.instance, origin=registry_entry_origin(entry.metadata)
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

    async def get_async(self, name: str) -> TechniqueInstance | None:
        """
        Get safe settings for a named factory.

        Returns:
            TechniqueInstance | None: The factory settings, if registered.
        """
        entry = self._registry.instances.get_entry(name)
        if entry is None:
            return None
        return await asyncio.to_thread(
            technique_to_instance, name=name, factory=entry.instance, origin=registry_entry_origin(entry.metadata)
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
            NotSaveableError: If a technique to save couldn't be restored as it is: its attack type can't be saved
                or a scan's launch script added it, an attack parameter holds a credential or leaves a setting to
                this server's environment, or it uses a Temporary entry.
            SavedEntryConflictError: If a technique to save has the name or a tag of a registered technique.
            SavedEntryStorageError: If the recipe can't be written, or can't be deleted after registering failed.
        """
        if request.persist:
            return await self._save_async(request=request, owner=owner, recipes_source=recipes_source)
        return await asyncio.to_thread(self._create, request)

    def _create(self, request: CreateTechniqueRequest) -> TechniqueInstance:
        with record_private_references() as private_references:
            factory = self._build(request)
        result = technique_to_instance(name=request.name, factory=factory, origin=RegistryEntryOrigin.TEMPORARY)
        # A technique built on the caller's private targets or converters is private to the caller too.
        metadata = creator_metadata(
            name=request.name, requested_private=request.private, private_references=private_references
        )
        self._registry.instances.register_runtime(factory, metadata=metadata)
        return result

    def _build(self, request: CreateTechniqueRequest) -> AttackTechniqueFactory:
        args = request.model_dump(exclude_unset=True, exclude={"name", "type", "tags", "persist", "private"})
        return self._registry.create_factory(
            name=request.name, attack_type=request.type, technique_tags=request.tags, strict_json=True, **args
        )

    async def check_saveability_async(self, *, request: TechniqueSaveabilityRequest) -> SaveabilityResponse:
        """
        Say whether a technique could be saved as asked, without building or saving it.

        Only the attack type, its parameters, and the entries the technique names are checked, by the checks saving
        runs before the technique is built. The name and tags aren't checked, and the technique isn't built, so no
        reason means the save checks pass, not that creating the technique succeeds.

        Args:
            request (TechniqueSaveabilityRequest): The technique to check.

        Returns:
            SaveabilityResponse: Why the technique can't be saved, or no reason when the save checks pass.

        Raises:
            ValueError: If the attack type isn't registered, or an address an attack parameter gives isn't a full
                http(s) or ws(s) address.
        """
        try:
            await check_saved_technique_async(recipe=TechniqueRecipe(**request.model_dump(exclude_unset=True)))
        except NotSaveableError as error:
            return SaveabilityResponse(not_saveable_reason=error.cause)
        return SaveabilityResponse()

    async def _save_async(
        self, *, request: CreateTechniqueRequest, owner: SavedEntryOwner | None, recipes_source: str | None
    ) -> TechniqueInstance:
        """
        Check the requested technique as restore would, build it, then save its recipe and register it as saved.

        The recipe is the request without its name, which restore builds with the same call.

        Returns:
            TechniqueInstance: The saved technique.

        Raises:
            NotSaveableError: If the technique's attack type was added by a scan's launch script or can't be saved,
                an attack parameter holds a credential or leaves a setting to this server's environment, or the
                technique uses a Temporary entry.
            ValueError: If the attack type isn't registered, or an address an attack parameter gives isn't a full
                http(s) or ws(s) address.
            SavedEntryConflictError: If the name or a tag conflicts with a registered technique.
            SavedEntryStorageError: If the recipe can't be written, or can't be deleted after registering failed.
        """
        recipe_fields = request.model_dump(exclude_unset=True, exclude={"name", "persist", "private"})
        recipe = TechniqueRecipe(**recipe_fields, owner=owner)
        await check_saved_technique_async(recipe=recipe)
        with record_private_references() as private_references:
            factory = await asyncio.to_thread(self._build, request)
        try:
            await asyncio.to_thread(self._registry.instances.check_runtime_available, factory)
        except TechniqueSelectorConflictError as error:
            raise SavedEntryConflictError(str(error)) from error
        result = await asyncio.to_thread(
            technique_to_instance, name=request.name, factory=factory, origin=RegistryEntryOrigin.SAVED
        )
        creator = creator_metadata(
            name=request.name, requested_private=request.private, private_references=private_references
        )
        await save_then_register_async(
            kind=RegistryRecipeKind.TECHNIQUES,
            name=request.name,
            recipe=recipe,
            register=lambda metadata: self._registry.instances.register_runtime(
                factory, metadata={**creator, **metadata}
            ),
            source=recipes_source,
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
