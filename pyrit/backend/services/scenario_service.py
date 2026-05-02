# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario service for listing available scenarios.

Provides read-only access to the ScenarioRegistry, exposing scenario metadata
through the REST API.
"""

from functools import lru_cache
from typing import Optional

from pyrit.backend.models.common import PaginationInfo
from pyrit.backend.models.scenarios import ScenarioListResponse, ScenarioSummary
from pyrit.registry import ScenarioMetadata, ScenarioRegistry


def _metadata_to_summary(metadata: ScenarioMetadata) -> ScenarioSummary:
    """
    Convert a ScenarioMetadata dataclass to a ScenarioSummary Pydantic model.

    Args:
        metadata: The registry metadata for a scenario.

    Returns:
        ScenarioSummary Pydantic model.
    """
    return ScenarioSummary(
        scenario_name=metadata.registry_name,
        scenario_type=metadata.class_name,
        description=metadata.class_description,
        default_strategy=metadata.default_strategy,
        aggregate_strategies=list(metadata.aggregate_strategies),
        all_strategies=list(metadata.all_strategies),
        default_datasets=list(metadata.default_datasets),
        max_dataset_size=metadata.max_dataset_size,
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
        cursor: Optional[str] = None,
    ) -> ScenarioListResponse:
        """
        List all available scenarios with pagination.

        Args:
            limit: Maximum items to return per page.
            cursor: Pagination cursor (scenario_name to start after).

        Returns:
            ScenarioListResponse with paginated scenario summaries.
        """
        all_metadata = self._registry.list_metadata()
        all_summaries = [_metadata_to_summary(m) for m in all_metadata]

        page, has_more = self._paginate(items=all_summaries, cursor=cursor, limit=limit)
        next_cursor = page[-1].scenario_name if has_more and page else None

        return ScenarioListResponse(
            items=page,
            pagination=PaginationInfo(limit=limit, has_more=has_more, next_cursor=next_cursor, prev_cursor=cursor),
        )

    async def get_scenario_async(self, *, scenario_name: str) -> Optional[ScenarioSummary]:
        """
        Get a single scenario by registry name.

        Args:
            scenario_name: The registry key of the scenario (e.g., 'foundry.red_team_agent').

        Returns:
            ScenarioSummary if found, None otherwise.
        """
        all_metadata = self._registry.list_metadata()
        for metadata in all_metadata:
            if metadata.registry_name == scenario_name:
                return _metadata_to_summary(metadata)
        return None

    @staticmethod
    def _paginate(
        *,
        items: list[ScenarioSummary],
        cursor: Optional[str],
        limit: int,
    ) -> tuple[list[ScenarioSummary], bool]:
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
