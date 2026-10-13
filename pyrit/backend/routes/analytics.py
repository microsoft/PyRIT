# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Read-only saved-result analytics through the shared SDK."""

from typing import Annotated

from fastapi import APIRouter, Depends

from pyrit.analytics import AttackResultAnalytics
from pyrit.backend.models.common import ProblemDetail
from pyrit.backend.services.analytics_service import get_analytics_service_async
from pyrit.models import (
    AttackAnalyticsFacetQuery,
    AttackAnalyticsFacets,
    AttackAnalyticsQuery,
    AttackAnalyticsReport,
    AttackAnalyticsResults,
    AttackAnalyticsResultsQuery,
)

router = APIRouter(
    prefix="/analytics/attacks",
    tags=["analytics"],
    responses={
        400: {"model": ProblemDetail, "description": "Invalid or mismatched result cursor"},
        422: {"model": ProblemDetail, "description": "Invalid analytics query"},
        500: {"model": ProblemDetail, "description": "Stored data or unexpected query failure"},
        503: {"model": ProblemDetail, "description": "Analytics capacity is busy; retry after one second"},
        504: {"model": ProblemDetail, "description": "The analytics query exceeded its deadline"},
    },
)


@router.post("/query", response_model=AttackAnalyticsReport)
async def query_analytics_async(
    *, query: AttackAnalyticsQuery, analytics: Annotated[AttackResultAnalytics, Depends(get_analytics_service_async)]
) -> AttackAnalyticsReport:
    """Return SDK statistics and a coherent first result page."""
    return await analytics.query_async(query=query)


@router.post("/results", response_model=AttackAnalyticsResults)
async def query_results_async(
    *,
    query: AttackAnalyticsResultsQuery,
    analytics: Annotated[AttackResultAnalytics, Depends(get_analytics_service_async)],
) -> AttackAnalyticsResults:
    """Return a fresh lightweight page without recalculating reports."""
    return await analytics.results_async(query=query)


@router.post("/facets", response_model=AttackAnalyticsFacets)
async def query_facets_async(
    *,
    query: AttackAnalyticsFacetQuery,
    analytics: Annotated[AttackResultAnalytics, Depends(get_analytics_service_async)],
) -> AttackAnalyticsFacets:
    """Return options for one dimension without recalculating reports."""
    return await analytics.facets_async(query=query)
