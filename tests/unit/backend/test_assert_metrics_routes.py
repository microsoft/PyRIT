# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""HTTP contract for ASSERT policy metrics using the real SDK computation."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from pyrit.analytics.assert_policy_metrics import _rate
from pyrit.backend.main import app
from pyrit.models import AssertPolicyMetrics, ScenarioResult
from pyrit.models.results.assert_policy_metrics import AssertNotJudgedCounts, AssertTechniqueMetrics
from unit.mocks import make_scenario_result

_URL = "/api/scenarios/runs/saved/assert-metrics"


@pytest.fixture
def client(compatibility_headers: dict[str, str]) -> TestClient:
    return TestClient(app, headers=compatibility_headers)


def _assert_result() -> ScenarioResult:
    return make_scenario_result(scenario_name="AssertPolicy", attack_results={})


@pytest.mark.usefixtures("patch_central_database")
def test_assert_metrics_computes_for_an_assert_run(client: TestClient) -> None:
    with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as service:
        service.return_value.get_run_results_async = AsyncMock(return_value=_assert_result())
        response = client.get(_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["split_unavailable_reason"] == "no_judged_results"
    assert body["techniques"] == {}


def test_assert_metrics_serializes_technique_rates(client: TestClient) -> None:
    rate = _rate(flagged=1, applicable=4, not_applicable=1)
    metrics = AssertPolicyMetrics(
        scenario_result_id="saved",
        judgment_mode="category_validated",
        total=5,
        scored_total=5,
        techniques={
            "single_turn": AssertTechniqueMetrics(
                technique="single_turn",
                total=5,
                scored_total=5,
                not_judged=AssertNotJudgedCounts(),
                policy_violation_on_not_permissible=rate,
                policy_violation_on_permissible=rate,
                dimensions={"policy_violation": rate},
            )
        },
    )
    with (
        patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as service,
        patch("pyrit.backend.routes.scenarios.compute_assert_policy_metrics_async", AsyncMock(return_value=metrics)),
    ):
        service.return_value.get_run_results_async = AsyncMock(return_value=_assert_result())
        response = client.get(_URL)

    assert response.status_code == 200
    assert AssertPolicyMetrics.model_validate(response.json()) == metrics


@pytest.mark.parametrize(
    ("saved", "error", "expected"),
    [
        (None, None, 404),
        (None, ValueError("Results are only available for completed runs."), 409),
        (make_scenario_result(scenario_name="ContentHarms", attack_results={}), None, 400),
    ],
)
def test_assert_metrics_returns_explicit_errors(
    client: TestClient, saved: ScenarioResult | None, error: ValueError | None, expected: int
) -> None:
    with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as service:
        service.return_value.get_run_results_async = AsyncMock(return_value=saved, side_effect=error)
        response = client.get(_URL)

    assert response.status_code == expected
    assert response.json()["detail"]


@pytest.mark.usefixtures("patch_central_database")
def test_assert_metrics_reports_internal_failures_as_server_errors(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(app, headers=compatibility_headers, raise_server_exceptions=False)
    with (
        patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as service,
        patch(
            "pyrit.backend.routes.scenarios.compute_assert_policy_metrics_async",
            AsyncMock(side_effect=ValueError("invalid stored identifier")),
        ),
    ):
        service.return_value.get_run_results_async = AsyncMock(return_value=_assert_result())
        response = client.get(_URL)

    assert response.status_code == 500
