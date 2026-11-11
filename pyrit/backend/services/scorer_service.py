# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Service for scorer class discovery and registered instances."""

import asyncio
from functools import lru_cache
from typing import Any

from pyrit.backend.models import (
    CreateScorerRequest,
    PaginationInfo,
    ScorerListResponse,
    ScorerTypeEntry,
    ScorerTypeResponse,
)
from pyrit.backend.models.scorers import UnregisteredScorer
from pyrit.backend.services.saved_entries import (
    SavedEntryConflictError,
    prepare_saved_params_async,
    save_then_register_async,
)
from pyrit.models import ScorerEvaluationIdentifier, ScorerIdentifier, project_behavioral_identity
from pyrit.models.catalog import (
    ComponentRecipe,
    RegistryEntryOrigin,
    RegistryRecipeKind,
    SavedEntryOwner,
    ScorerInstance,
)
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.output.scorer.json import format_scorer_type, scorer_metrics_to_dict
from pyrit.registry import ScorerRegistry
from pyrit.registry.instance_registry import RegistryNameTakenError, registry_entry_origin, saved_entry_owner
from pyrit.score import Scorer, TrueFalseScorer


class ScorerService:
    """Expose ScorerRegistry metadata and instances without duplicating construction logic."""

    def __init__(self) -> None:
        """Initialize the service with the scorer registry singleton."""
        self._registry = ScorerRegistry.get_registry_singleton()

    def _build_instance(
        self,
        *,
        name: str,
        scorer: Any,
        origin: RegistryEntryOrigin | None,
        owner: SavedEntryOwner | None = None,
        current_user_oid: str | None = None,
    ) -> ScorerInstance:
        from pyrit.backend.services.chat_scoring_service import ChatScoringService

        metadata = self._registry.get_registered_class_metadata(scorer.__class__.__name__)
        identifier = ScorerIdentifier.from_component_identifier(scorer.get_identifier())
        evaluation_identifier = project_behavioral_identity(identifier, identifier_type=ScorerIdentifier)
        return ScorerInstance(
            scorer_registry_name=name,
            identifier=identifier,
            display_type=format_scorer_type(identifier),
            scorable_types=sorted(
                kind.model_fields["scorable_type"].default for kind in scorer.get_supported_scorable_types()
            ),
            condition_types=sorted(kind.__name__ for kind in scorer.get_condition_types()),
            evaluation_identifier=evaluation_identifier.with_eval_hash(
                ScorerEvaluationIdentifier(identifier).eval_hash
            ),
            metrics=scorer_metrics_to_dict(scorer.get_scorer_metrics()),
            description=metadata.class_description or None if metadata else None,
            chat_scoring_disabled_reason=ChatScoringService.disabled_reason(scorer),
            origin=origin,
            saved_by=(owner.name or None) if owner is not None else None,
            saved_by_me=owner is not None and current_user_oid is not None and owner.oid == current_user_oid,
        )

    def resolve_registered_scorer(self, reference: RegisteredComponentReference) -> Scorer:
        """
        Resolve the exact configured instance without silently replacing its identity.

        Returns:
            The configured response scorer.

        Raises:
            ValueError: The selected scorer is missing, changed, or incompatible.
        """
        scorer = self._registry.instances.get(reference.registry_name)
        if scorer is None:
            raise ValueError("The selected chat scorer is no longer registered.")
        if scorer.get_identifier().hash != reference.identifier_hash:
            raise ValueError("The selected chat scorer configuration changed. Select it again.")
        return scorer

    def find_registered_scorer(self, identifier_hash: str) -> Scorer | None:
        """
        Find the exact recorded scorer even when its registry alias differs.

        Returns:
            An existing configured scorer, or None when no exact match is registered.
        """
        return next(
            (
                entry.instance
                for entry in self._registry.instances.get_all_instances()
                if entry.instance.get_identifier().hash == identifier_hash
            ),
            None,
        )

    async def list_scorer_types_async(self) -> ScorerTypeResponse:
        """
        List the scorer types external callers can build, without constructing scorers.

        Each entry lists only the parameters external callers may supply, each
        described in the form callers send it; types that need a Python object for a
        required parameter are left out. Parameters that name a file on the server,
        and types that require one, are left out too, so callers cannot choose a
        server file for a scorer to read.

        Returns:
            ScorerTypeResponse: Scorer type metadata for external callers.
        """

        def list_types() -> ScorerTypeResponse:
            items = [
                ScorerTypeEntry(
                    scorer_type=metadata.class_name,
                    parameters=[
                        parameter.for_external_catalog()
                        for parameter in metadata.parameters
                        if parameter.is_allowed_external_input()
                    ],
                    is_llm_based=metadata.is_llm_based,
                    supports_objective_scoring=issubclass(
                        self._registry.get_class(metadata.class_name), TrueFalseScorer
                    ),
                    description=metadata.class_description or None,
                )
                for metadata in self._registry.get_all_registered_class_metadata()
                if all(parameter.is_allowed_external_input() for parameter in metadata.parameters if parameter.required)
            ]
            return ScorerTypeResponse(items=items)

        return await asyncio.to_thread(list_types)

    async def list_scorers_async(
        self, *, limit: int = 50, cursor: str | None = None, current_user_oid: str | None = None
    ) -> ScorerListResponse:
        """
        List named scorer instances in stable registry-name order.

        Args:
            limit (int): The most scorers to return. Defaults to 50.
            cursor (str | None): The registry name to start after, or None for the first page. Defaults to None.
            current_user_oid (str | None): The signed-in user's Microsoft Entra object ID, used to mark the scorers
                they saved. Defaults to None.

        Returns:
            ScorerListResponse: A page and its pagination metadata.

        Raises:
            ValueError: If the cursor does not name a registered scorer instance.
        """

        def list_instances() -> ScorerListResponse:
            entries = self._registry.instances.get_all_instances()
            start = next((index + 1 for index, entry in enumerate(entries) if entry.name == cursor), None)
            if cursor and start is None:
                # A cursor naming an entry that no longer exists must not silently
                # restart at the first page, which would serve duplicate rows.
                raise ValueError("The cursor is not valid for the scorer registry")
            start = start or 0
            page = entries[start : start + limit]
            has_more = len(entries) > start + limit
            return ScorerListResponse(
                items=[
                    self._build_instance(
                        name=entry.name,
                        scorer=entry.instance,
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

        return await asyncio.to_thread(list_instances)

    async def get_scorer_async(
        self, *, scorer_registry_name: str, current_user_oid: str | None = None
    ) -> ScorerInstance | None:
        """
        Get one registered scorer by name.

        Args:
            scorer_registry_name (str): The scorer's registry name.
            current_user_oid (str | None): The signed-in user's Microsoft Entra object ID, used to mark a scorer
                they saved. Defaults to None.

        Returns:
            ScorerInstance | None: The matching scorer, if present.
        """

        def get_instance() -> ScorerInstance | None:
            entry = self._registry.instances.get_entry(scorer_registry_name)
            if entry is None:
                return None
            return self._build_instance(
                name=scorer_registry_name,
                scorer=entry.instance,
                origin=registry_entry_origin(entry.metadata),
                owner=saved_entry_owner(entry.metadata),
                current_user_oid=current_user_oid,
            )

        return await asyncio.to_thread(get_instance)

    async def create_scorer_async(
        self,
        *,
        request: CreateScorerRequest,
        owner: SavedEntryOwner | None = None,
        recipes_source: str | None = None,
    ) -> ScorerInstance | UnregisteredScorer:
        """
        Build a scorer through the shared registry resolver, with optional registration.

        A parameter that names a file on the server is rejected before the scorer is
        built, so the server never reads a file a caller chose. With ``persist``, the
        scorer is saved so it is restored at every start; otherwise it is Temporary.

        Args:
            request: The create scorer request with type, params, and name.
            owner: The signed-in user, recorded on a saved scorer, or None when sign-in is off.
            recipes_source: Where saved entries are stored, or None for the default folder.

        Returns:
            ScorerInstance | UnregisteredScorer: A named instance, or an unregistered
                descriptor when ``register`` is false.

        Raises:
            ValueError: If the scorer type is unknown, a parameter names a file on the
                server, or the registry or scorer rejects the parameters.
            NotSaveableError: If a scorer to save would hold or use a credential, or names a Temporary entry.
            SavedEntryConflictError: If a scorer to save has the name of a registered or saved scorer.
            SavedEntryStorageError: If a scorer to save can't be written to storage.
        """
        if request.persist and request.name is not None:
            return await self._save_scorer_async(
                name=request.name, request=request, owner=owner, recipes_source=recipes_source
            )

        def create() -> ScorerInstance | UnregisteredScorer:
            if request.type not in self._registry:
                raise ValueError(f"Scorer type '{request.type}' not found")
            if not request.register:
                scorer = self._registry.create_instance_from_external_input(request.type, params=request.params)
                return UnregisteredScorer(
                    identifier=ScorerIdentifier.from_component_identifier(scorer.get_identifier())
                )
            if request.name is None:
                raise ValueError("name is required when register=true")
            scorer = self._registry.create_named_instance(
                name=request.name,
                type_name=request.type,
                params=request.params,
                external_input=True,
            )
            return self._build_instance(name=request.name, scorer=scorer, origin=RegistryEntryOrigin.TEMPORARY)

        return await asyncio.to_thread(create)

    async def _save_scorer_async(
        self,
        *,
        name: str,
        request: CreateScorerRequest,
        owner: SavedEntryOwner | None,
        recipes_source: str | None,
    ) -> ScorerInstance:
        """
        Build the requested scorer, save its recipe, then register it as a saved entry.

        The recipe names the class by the registry name the request used, which restore looks up.

        Returns:
            ScorerInstance: The saved scorer.

        Raises:
            ValueError: If the scorer type is unknown, or the registry or scorer rejects the parameters.
            NotSaveableError: If the scorer would hold or use a credential, or names a Temporary entry.
            SavedEntryConflictError: If a saved or registered scorer already has the name.
            SavedEntryStorageError: If the recipe can't be written, or can't be deleted after registering failed.
        """

        def find_class() -> type:
            if request.type not in self._registry:
                raise ValueError(f"Scorer type '{request.type}' not found")
            return self._registry.get_class(request.type)

        # Finding the class can run the registry's first, slow class discovery, so it stays off the event loop.
        scorer_cls = await asyncio.to_thread(find_class)
        params = await prepare_saved_params_async(
            registry=self._registry, type_name=request.type, cls=scorer_cls, params=request.params
        )

        def build() -> tuple[Any, ScorerInstance]:
            try:
                self._registry.instances.validate_name_available(name)
            except RegistryNameTakenError as error:
                raise SavedEntryConflictError(str(error)) from error
            scorer = self._registry.create_instance_from_external_input(request.type, params=params)
            # The user saving the scorer is the one asking, so the response marks it as theirs.
            return scorer, self._build_instance(
                name=name,
                scorer=scorer,
                origin=RegistryEntryOrigin.SAVED,
                owner=owner,
                current_user_oid=owner.oid if owner is not None else None,
            )

        scorer, instance = await asyncio.to_thread(build)
        await save_then_register_async(
            kind=RegistryRecipeKind.SCORERS,
            name=name,
            recipe=ComponentRecipe(
                class_module=scorer_cls.__module__, class_name=request.type, params=params, owner=owner
            ),
            recipes_source=recipes_source,
            register=lambda metadata: self._registry.instances.register(scorer, name=name, metadata=metadata),
        )
        return instance


@lru_cache(maxsize=1)
def get_scorer_service() -> ScorerService:
    """Return the cached scorer service."""
    return ScorerService()
