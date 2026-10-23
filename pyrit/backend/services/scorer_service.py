# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Service for scorer class discovery and registered instances."""

import asyncio
from functools import lru_cache
from typing import Any

from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.scorers import (
    CreateScorerRequest,
    ScorerListResponse,
    ScorerTypeEntry,
    ScorerTypeResponse,
    UnregisteredScorer,
)
from pyrit.models import ScorerEvaluationIdentifier, project_behavioral_identity
from pyrit.models.catalog.scorer import ScorerInstance
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.models.identifiers.scorer_identifier import ScorerIdentifier
from pyrit.output.scorer.json import format_scorer_type, scorer_metrics_to_dict
from pyrit.registry import ScorerRegistry
from pyrit.score import Scorer


class ScorerService:
    """Expose ScorerRegistry metadata and instances without duplicating construction logic."""

    def __init__(self) -> None:
        """Initialize the service with the scorer registry singleton."""
        self._registry = ScorerRegistry.get_registry_singleton()

    def _build_instance(self, *, name: str, scorer: Any) -> ScorerInstance:
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
        required parameter are left out.

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
                        if parameter.is_external_input
                    ],
                    is_llm_based=metadata.is_llm_based,
                    description=metadata.class_description or None,
                )
                for metadata in self._registry.get_all_registered_class_metadata()
                if all(parameter.is_external_input for parameter in metadata.parameters if parameter.required)
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
            return ScorerListResponse(
                items=[self._build_instance(name=entry.name, scorer=entry.instance) for entry in page],
                pagination=PaginationInfo(
                    limit=limit,
                    has_more=has_more,
                    next_cursor=page[-1].name if page and has_more else None,
                    prev_cursor=cursor,
                ),
            )

        return await asyncio.to_thread(list_instances)

    async def get_scorer_async(self, *, scorer_registry_name: str) -> ScorerInstance | None:
        """
        Get one registered scorer by name.

        Returns:
            ScorerInstance | None: The matching scorer, if present.
        """

        def get_instance() -> ScorerInstance | None:
            scorer = self._registry.instances.get(scorer_registry_name)
            return self._build_instance(name=scorer_registry_name, scorer=scorer) if scorer is not None else None

        return await asyncio.to_thread(get_instance)

    async def create_scorer_async(self, *, request: CreateScorerRequest) -> ScorerInstance | UnregisteredScorer:
        """
        Build and register a scorer through the shared registry resolver.

        Returns:
            ScorerInstance: The registered scorer and its identifier.
        """

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
            return self._build_instance(name=request.name, scorer=scorer)

        return await asyncio.to_thread(create)


@lru_cache(maxsize=1)
def get_scorer_service() -> ScorerService:
    """Return the cached scorer service."""
    return ScorerService()
