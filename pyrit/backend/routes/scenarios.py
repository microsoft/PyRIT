# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario API routes.

Provides endpoints for listing available scenarios, their metadata,
and managing scenario runs.

Route structure:
    /api/scenarios/catalog       — scenario catalog (list + detail)
    /api/scenarios/runs          — scenario execution lifecycle
"""

from fastapi import APIRouter, HTTPException, Query, status

from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.models.scenarios import (
    ListRegisteredScenariosResponse,
    RegisteredScenario,
    RunScenarioRequest,
    ScenarioRunListResponse,
    ScenarioRunSummary,
)
from pyrit.backend.services.scenario_run_service import get_scenario_run_service
from pyrit.backend.services.scenario_service import get_scenario_service
from pyrit.models.scenario_result import ScenarioResult

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


# ============================================================================
# Scenario Catalog
# ============================================================================


@router.get(
    "/catalog",
    response_model=ListRegisteredScenariosResponse,
)
async def list_scenarios(  # pyrit-async-suffix-exempt
    limit: int = Query(50, ge=1, le=200, description="Maximum items per page"),
    cursor: str | None = Query(None, description="Pagination cursor (scenario_name to start after)"),
) -> ListRegisteredScenariosResponse:
    """
    List all available scenarios.

    Returns scenario metadata including strategies, datasets, and defaults.
    Use GET /api/scenarios/catalog/{scenario_name} for full details on a specific scenario.

    Returns:
        ScenarioListResponse: Paginated list of scenario summaries.
    """
    service = get_scenario_service()
    return await service.list_scenarios_async(limit=limit, cursor=cursor)


@router.get(
    "/catalog/{scenario_name:path}",
    response_model=RegisteredScenario,
    responses={
        404: {"model": ProblemDetail, "description": "Scenario not found"},
    },
)
async def get_scenario(scenario_name: str) -> RegisteredScenario:  # pyrit-async-suffix-exempt
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


# ============================================================================
# Scenario Runs
# ============================================================================


@router.post(
    "/runs",
    response_model=ScenarioRunSummary,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"model": ProblemDetail, "description": "Invalid request (bad scenario/target/strategy)"},
    },
)
async def start_scenario_run(request: RunScenarioRequest) -> ScenarioRunSummary:  # pyrit-async-suffix-exempt
    """
    Start a new scenario run as a background task.

    Returns immediately with a scenario_result_id that can be polled for status.

    Args:
        request: Scenario run configuration.

    Returns:
        ScenarioRunSummary: Run metadata with PENDING status.
    """
    service = get_scenario_run_service()
    try:
        return await service.start_run_async(request=request)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from None


@router.get(
    "/runs",
    response_model=ScenarioRunListResponse,
)
async def list_scenario_runs(limit: int = Query(100, ge=1)) -> ScenarioRunListResponse:  # pyrit-async-suffix-exempt
    """
    List tracked scenario runs (most recent first).

    Args:
        limit (int): Maximum number of runs to return. Defaults to 100.

    Returns:
        ScenarioRunListResponse: Runs, most recent first.
    """
    service = get_scenario_run_service()
    return service.list_runs(limit=limit)


@router.get(
    "/runs/{scenario_result_id}",
    response_model=ScenarioRunSummary,
    responses={
        404: {"model": ProblemDetail, "description": "Run not found"},
    },
)
async def get_scenario_run(scenario_result_id: str) -> ScenarioRunSummary:  # pyrit-async-suffix-exempt
    """
    Get the current status and result of a scenario run.

    Args:
        scenario_result_id: The scenario_result_id returned by POST /runs.

    Returns:
        ScenarioRunSummary: Current run status (and result if completed).
    """
    service = get_scenario_run_service()
    run = service.get_run(scenario_result_id=scenario_result_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scenario run '{scenario_result_id}' not found",
        )
    return run


@router.post(
    "/runs/{scenario_result_id}/cancel",
    response_model=ScenarioRunSummary,
    responses={
        404: {"model": ProblemDetail, "description": "Run not found"},
        409: {"model": ProblemDetail, "description": "Run already in terminal state"},
    },
)
async def cancel_scenario_run(scenario_result_id: str) -> ScenarioRunSummary:  # pyrit-async-suffix-exempt
    """
    Cancel a running scenario.

    Args:
        scenario_result_id: The scenario_result_id to cancel.

    Returns:
        ScenarioRunSummary: Updated run with CANCELLED status.
    """
    service = get_scenario_run_service()
    try:
        result = await service.cancel_run_async(scenario_result_id=scenario_result_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from None

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scenario run '{scenario_result_id}' not found",
        )
    return result


@router.get(
    "/runs/{scenario_result_id}/results",
    response_model=ScenarioResult,
    responses={
        404: {"model": ProblemDetail, "description": "Run not found"},
        409: {"model": ProblemDetail, "description": "Run not yet completed"},
    },
)
async def get_scenario_run_results(scenario_result_id: str) -> ScenarioResult:  # pyrit-async-suffix-exempt
    """
    Get detailed results for a completed scenario run.

    Args:
        scenario_result_id: The scenario_result_id.

    Returns:
        ScenarioResult: Detailed run results. FastAPI handles JSON serialization.
    """
    service = get_scenario_run_service()
    try:
        result = service.get_run_results(scenario_result_id=scenario_result_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from None

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scenario run '{scenario_result_id}' not found",
        )
    return result
