# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario API routes.

Provides endpoints for listing available scenarios and their metadata.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.scenarios import ScenarioListResponse, ScenarioSummary
from pyrit.backend.services.scenario_service import get_scenario_service

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get(
    "",
    response_model=ScenarioListResponse,
)
async def list_scenarios(
    limit: int = Query(50, ge=1, le=200, description="Maximum items per page"),
    cursor: Optional[str] = Query(None, description="Pagination cursor (scenario_name to start after)"),
) -> ScenarioListResponse:
    """
    List all available scenarios.

    Returns scenario metadata including strategies, datasets, and defaults.
    Use GET /api/scenarios/{scenario_name} for full details on a specific scenario.

    Returns:
        ScenarioListResponse: Paginated list of scenario summaries.
    """
    service = get_scenario_service()
    return await service.list_scenarios_async(limit=limit, cursor=cursor)


@router.get(
    "/{scenario_name:path}",
    response_model=ScenarioSummary,
    responses={
        404: {"model": ProblemDetail, "description": "Scenario not found"},
    },
)
async def get_scenario(scenario_name: str) -> ScenarioSummary:
    """
    Get details for a specific scenario.

    Args:
        scenario_name: Registry name of the scenario (e.g., 'foundry.red_team_agent').

    Returns:
        ScenarioSummary: Full scenario metadata.
    """
    service = get_scenario_service()

    scenario = await service.get_scenario_async(scenario_name=scenario_name)
    if not scenario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scenario '{scenario_name}' not found",
        )

    return scenario
