# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario service for listing available scenarios.

Provides read-only access to the ScenarioRegistry, exposing scenario metadata
through the REST API.
"""

from functools import lru_cache

from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.scenarios import ListRegisteredScenariosResponse
from pyrit.models.catalog.scenario import (
    RegisteredScenario,
)
from pyrit.registry import ScenarioMetadata, ScenarioRegistry


def _metadata_to_registered_scenario(metadata: ScenarioMetadata) -> RegisteredScenario:
    """
    Convert a ScenarioMetadata dataclass to a ScenarioSummary Pydantic model.

    Args:
        metadata: The registry metadata for a scenario.

    Returns:
        ScenarioSummary Pydantic model.
    """
    return RegisteredScenario(
        scenario_name=metadata.registry_name,
        scenario_type=metadata.class_name,
        description=metadata.class_description,
        default_technique=metadata.default_technique,
        aggregate_techniques=list(metadata.aggregate_techniques),
        all_techniques=list(metadata.all_techniques),
        default_datasets=list(metadata.default_datasets),
        supported_parameters=list(metadata.supported_parameters),
    )


class ScenarioService:
    """
    Service for listing available scenarios.

    Uses ScenarioRegistry as the source of truth for scenario metadata.
    """

    def __init__(self) -> None:
        """Initialize the scenario service."""
        self._registry = ScenarioRegistry.get_registry_singleton()

    async def list_scenarios_async(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ListRegisteredScenariosResponse:
        """
        List all available scenarios with pagination.

        Args:
            limit: Maximum items to return per page.
            cursor: Pagination cursor (scenario_name to start after).

        Returns:
            ScenarioListResponse with paginated scenario summaries.
        """
        all_metadata = self._registry.get_all_registered_class_metadata()
        all_summaries = [_metadata_to_registered_scenario(m) for m in all_metadata]

        page, has_more = self._paginate(items=all_summaries, cursor=cursor, limit=limit)
        next_cursor = page[-1].scenario_name if has_more and page else None

        return ListRegisteredScenariosResponse(
            items=page,
            pagination=PaginationInfo(
                limit=limit,
                has_more=has_more,
                next_cursor=next_cursor,
                prev_cursor=cursor,
            ),
        )

    async def get_scenario_async(self, *, scenario_name: str) -> RegisteredScenario | None:
        """
        Get a single scenario by registry name.

        Args:
            scenario_name: The registry key of the scenario (e.g., 'foundry.red_team_agent').

        Returns:
            ScenarioSummary if found, None otherwise.
        """
        metadata = self._registry.get_registered_class_metadata(scenario_name)
        if metadata is not None:
            return _metadata_to_registered_scenario(metadata)
        return None

    @staticmethod
    def _paginate(
        *,
        items: list[RegisteredScenario],
        cursor: str | None,
        limit: int,
    ) -> tuple[list[RegisteredScenario], bool]:
        """
        Apply cursor-based pagination.

        Args:
            items: Full list of items.
            cursor: Scenario name to start after.
            limit: Maximum items per page.

        Returns:
            Tuple of (paginated items, has_more flag).
        """
        start_idx = 0
        if cursor:
            for i, item in enumerate(items):
                if item.scenario_name == cursor:
                    start_idx = i + 1
                    break

        page = items[start_idx : start_idx + limit]
        has_more = len(items) > start_idx + limit
        return page, has_more


@lru_cache(maxsize=1)
def get_scenario_service() -> ScenarioService:
    """
    Get the global scenario service instance.

    Returns:
        The singleton ScenarioService instance.
    """
    return ScenarioService()
