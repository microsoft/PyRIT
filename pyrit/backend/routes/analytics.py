# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Read-only saved-result analytics through the shared SDK."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from pyrit.analytics import AttackResultAnalytics, NotAssertPolicyRunError, compute_assert_policy_metrics_async
from pyrit.backend.models import ProblemDetail
from pyrit.backend.models.common import MAX_IDENTIFIER_LENGTH, IdentifierStr
from pyrit.backend.services import get_scenario_run_service
from pyrit.backend.services.analytics_service import get_analytics_service_async
from pyrit.backend.services.scenario_run_service import ScenarioRunNotCompletedError
from pyrit.models import (
    AssertPolicyMetrics,
    AttackAnalyticsFacetQuery,
    AttackAnalyticsFacets,
    AttackAnalyticsQuery,
    AttackAnalyticsReport,
    AttackAnalyticsResults,
    AttackAnalyticsResultsQuery,
)

logger = logging.getLogger(__name__)
NOT_ASSERT_POLICY_RUN_TYPE = "/errors/not-assert-policy-run"

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    responses={
        400: {"model": ProblemDetail, "description": "Invalid or mismatched result cursor"},
        422: {"model": ProblemDetail, "description": "Invalid analytics query"},
        500: {"model": ProblemDetail, "description": "Stored data or unexpected query failure"},
        503: {"model": ProblemDetail, "description": "Analytics capacity is busy; retry after one second"},
        504: {"model": ProblemDetail, "description": "The analytics query exceeded its deadline"},
    },
)


@router.post("/attacks/query", response_model=AttackAnalyticsReport)
async def query_analytics_async(
    *, query: AttackAnalyticsQuery, analytics: Annotated[AttackResultAnalytics, Depends(get_analytics_service_async)]
) -> AttackAnalyticsReport:
    """Return SDK statistics and a coherent first result page."""
    return await analytics.query_async(query=query)


@router.post("/attacks/results", response_model=AttackAnalyticsResults)
async def query_results_async(
    *,
    query: AttackAnalyticsResultsQuery,
    analytics: Annotated[AttackResultAnalytics, Depends(get_analytics_service_async)],
) -> AttackAnalyticsResults:
    """Return a fresh lightweight page without recalculating reports."""
    return await analytics.results_async(query=query)


@router.post("/attacks/facets", response_model=AttackAnalyticsFacets)
async def query_facets_async(
    *,
    query: AttackAnalyticsFacetQuery,
    analytics: Annotated[AttackResultAnalytics, Depends(get_analytics_service_async)],
) -> AttackAnalyticsFacets:
    """Return options for one dimension without recalculating reports."""
    return await analytics.facets_async(query=query)


@router.get(
    "/assert",
    response_model=AssertPolicyMetrics,
    responses={
        400: {"model": ProblemDetail, "description": "Not an ASSERT policy run"},
        404: {"model": ProblemDetail, "description": "Run not found"},
        409: {"model": ProblemDetail, "description": "Run not yet completed"},
    },
)
async def get_assert_policy_metrics_async(
    *, request: Request, scenario_result_id: IdentifierStr = Query(min_length=1, max_length=MAX_IDENTIFIER_LENGTH)
) -> AssertPolicyMetrics | JSONResponse:
    """Return SDK ASSERT metrics for the completed run selected by scenario_result_id."""
    try:
        result = await get_scenario_run_service().get_run_results_async(scenario_result_id=scenario_result_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Scenario run '{scenario_result_id}' not found")
        return await compute_assert_policy_metrics_async(result)
    except ScenarioRunNotCompletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except NotAssertPolicyRunError as exc:
        problem = ProblemDetail(
            type=NOT_ASSERT_POLICY_RUN_TYPE,
            title="Not an ASSERT run",
            status=400,
            detail=str(exc),
            instance=request.url.path,
        )
        return JSONResponse(
            status_code=400,
            media_type="application/problem+json",
            content=problem.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        logger.exception("Could not compute ASSERT metrics for scenario run %s.", scenario_result_id)
        raise HTTPException(status_code=500, detail="ASSERT metrics could not be computed for this run.") from exc
