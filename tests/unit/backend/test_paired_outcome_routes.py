# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""HTTP contract for paired analytics using real SDK computations."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.common.utils import to_sha256
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanGroupKind,
    ScenarioRunPlanSeedGroup,
)
from unit.mocks import make_scenario_result


def _saved_result() -> ScenarioResult:
    plan = ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id=group,
                atomic_attack_name=group,
                display_group=group,
                technique_eval_hash=group,
                kind=kind,
                seed_group_ids=["a", "b", "c", "d", "missing"],
            )
            for group, kind in [
                ("baseline", ScenarioRunPlanGroupKind.BASELINE),
                ("attack", ScenarioRunPlanGroupKind.ATTACK),
            ]
        ],
        seed_groups=[
            ScenarioRunPlanSeedGroup(id=seed, objective=seed, objective_sha256=to_sha256(seed))
            for seed in ["a", "b", "c", "d", "missing"]
        ],
    )
    outcomes = {
        "baseline": [AttackOutcome.FAILURE, AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.SUCCESS],
        "attack": [AttackOutcome.SUCCESS, AttackOutcome.FAILURE, AttackOutcome.FAILURE, AttackOutcome.SUCCESS],
    }
    return make_scenario_result(
        metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
        attack_results={
            group: [
                AttackResult(
                    attack_result_id=str(uuid4()),
                    conversation_id=str(uuid4()),
                    objective=seed,
                    outcome=outcome,
                    timestamp=datetime(2026, 10, 20, tzinfo=UTC),
                    attribution_data={"seed_group_id": seed, "parent_eval_hash": group},
                )
                for seed, outcome in zip(["a", "b", "c", "d"], values, strict=True)
            ]
            for group, values in outcomes.items()
        },
    )


@pytest.fixture
def client(compatibility_headers: dict[str, str]) -> TestClient:
    return TestClient(app, headers=compatibility_headers)


def test_paired_outcomes_reports_transitions_and_shared_denominator(client: TestClient) -> None:
    with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as service:
        service.return_value.get_run_results_async = AsyncMock(return_value=_saved_result())
        response = client.get(
            "/api/scenarios/runs/saved/paired-outcomes",
            params={
                "baseline_group_id": "baseline",
                "attack_group_id": "attack",
            },
        )
    assert response.status_code == 200
    assert response.json() == {
        "baseline_group_id": "baseline",
        "attack_group_id": "attack",
        "planned_pairs": 5,
        "decided_pairs": 4,
        "excluded_pairs": 1,
        "failure_to_success": 1,
        "success_to_failure": 1,
        "failure_to_failure": 1,
        "success_to_success": 1,
        "baseline_success_rate": 0.5,
        "attack_success_rate": 0.5,
        "paired_difference": 0.0,
        "missing_baseline": 0,
        "missing_attack": 0,
        "missing_both": 1,
        "baseline_errors": 0,
        "attack_errors": 0,
        "baseline_undetermined": 0,
        "attack_undetermined": 0,
        "unmatched_baseline_seeds": 0,
        "unmatched_attack_seeds": 0,
        "unattributed_attempts": 0,
    }


@pytest.mark.parametrize(
    ("saved", "error", "expected"),
    [
        (None, None, 404),
        (None, ValueError("Results are only available for completed runs."), 409),
        (_saved_result(), None, 400),
    ],
)
def test_paired_outcomes_returns_explicit_errors(
    client: TestClient, saved: ScenarioResult | None, error: ValueError | None, expected: int
) -> None:
    with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as service:
        service.return_value.get_run_results_async = AsyncMock(return_value=saved, side_effect=error)
        response = client.get(
            "/api/scenarios/runs/saved/paired-outcomes",
            params={
                "baseline_group_id": "baseline",
                "attack_group_id": "unrelated",
            },
        )
    assert response.status_code == expected
    assert response.json()["detail"]


def test_paired_outcomes_requires_explicit_groups(client: TestClient) -> None:
    response = client.get("/api/scenarios/runs/saved/paired-outcomes")
    assert response.status_code == 422


def test_paired_outcomes_rejects_legacy_plan(client: TestClient) -> None:
    saved = _saved_result()
    saved.metadata = {}
    with patch("pyrit.backend.routes.scenarios.get_scenario_run_service") as service:
        service.return_value.get_run_results_async = AsyncMock(return_value=saved)
        response = client.get(
            "/api/scenarios/runs/saved/paired-outcomes",
            params={
                "baseline_group_id": "baseline",
                "attack_group_id": "attack",
            },
        )
    assert response.status_code == 400
    assert "saved scenario run plan" in response.json()["detail"]
