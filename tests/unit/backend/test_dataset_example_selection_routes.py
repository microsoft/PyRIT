# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""HTTP coverage for exact example estimate and launch preparation."""

from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from pyrit.backend.main import app
from pyrit.backend.services import get_scenario_service
from pyrit.backend.services.dataset_example_fingerprint import dataset_example_fingerprint
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import MemoryInterface, SeedEntry
from pyrit.models import Seed, SeedObjective, SeedPrompt
from pyrit.registry import ScenarioRegistry, TargetRegistry
from pyrit.scenario import AtomicAttack, DatasetAttackConfiguration, Scenario, ScenarioTechnique
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.score import SubStringScorer
from unit.mocks import MockPromptTarget

DATASET = "exact-route"
NAMED = f"dataset:named:{DATASET}"
SCENARIO_NAME = "test.exact_selection_2757"
TARGET_NAME = "exact-target-2757"


class _Technique(ScenarioTechnique):
    ALL = ("all", {"all"})
    DIRECT = ("direct", set())

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


class _RouteScenario(Scenario):
    prepared_counts: list[int] = []

    def __init__(self) -> None:
        super().__init__(
            version=1,
            technique_class=_Technique,
            objective_scorer=SubStringScorer(substring="yes"),
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["unused-default"]),
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        type(self).prepared_counts.append(len(context.seed_groups))
        raise RuntimeError("stop before execution")


@pytest.fixture
def registered_route_scenario() -> Iterator[None]:
    registry = ScenarioRegistry.get_registry_singleton()
    targets = TargetRegistry.get_registry_singleton()
    registry.register_class(_RouteScenario, name=SCENARIO_NAME)
    targets.instances.register(MockPromptTarget(), name=TARGET_NAME, replace=True)
    _RouteScenario.prepared_counts = []
    get_scenario_service.cache_clear()
    try:
        yield
    finally:
        registry._classes.pop(SCENARIO_NAME, None)
        registry._metadata_cache = None
        get_scenario_service.cache_clear()


@pytest.fixture
async def client(
    patch_central_database, compatibility_headers: dict[str, str], registered_route_scenario: None
) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=compatibility_headers) as http_client:
        yield http_client


async def _store(memory: MemoryInterface, *seeds: Seed) -> None:
    async with await memory.get_session_async() as session:
        session.add_all(SeedEntry(entry=seed) for seed in seeds)
        await session.commit()


async def _selection_body(memory: MemoryInterface, seeds: list[SeedObjective]) -> dict[str, object]:
    example_ids = [seed.id for seed in seeds]
    found = await memory.get_seed_examples_by_ids_async(dataset_name=DATASET, example_ids=example_ids)
    return {
        "example_selection": {
            "examples": [
                {
                    "selection_key": NAMED,
                    "example_id": str(example_id),
                    "fingerprint": dataset_example_fingerprint(
                        selection_key=NAMED, example_id=example_id, members=found[example_id]
                    ),
                }
                for example_id in example_ids
            ]
        }
    }


@pytest.mark.usefixtures("patch_central_database")
async def test_estimate_and_launch_use_six_examples_until_one_changes(
    client: AsyncClient, sqlite_instance: MemoryInterface
) -> None:
    seeds = [SeedObjective(value=f"goal {index}", dataset_name=DATASET, added_by="test") for index in range(6)]
    await _store(sqlite_instance, *seeds)
    body = await _selection_body(sqlite_instance, seeds)
    with (
        patch.object(SeedDatasetProvider, "fetch_dataset_async", new_callable=AsyncMock) as fetch_one,
        patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as fetch_many,
        patch.object(MemoryInterface, "add_seeds_to_memory_async", new_callable=AsyncMock) as add_seeds,
        patch.object(SeedEntry, "get_seed", side_effect=AssertionError("get_seed")),
    ):
        estimate = await client.post(f"/api/scenarios/catalog/{SCENARIO_NAME}/estimate", json=body)
        launch = await client.post(
            "/api/scenarios/runs",
            json={**body, "scenario_name": SCENARIO_NAME, "target_name": TARGET_NAME},
        )
    assert estimate.status_code == 200
    payload = estimate.json()
    assert payload["dataset_size"] == {"kind": "bounded", "value": 6}
    assert payload["effective_datasets"]["max_total"] == "all"
    assert payload["total_attack_count"] == 12
    assert "resolved example count" in payload["note"]
    assert launch.status_code == 500
    assert _RouteScenario.prepared_counts == [6]
    fetch_one.assert_not_called()
    fetch_many.assert_not_called()
    add_seeds.assert_not_called()

    async with await sqlite_instance.get_session_async() as session:
        entry = await session.get(SeedEntry, seeds[0].id)
        assert entry is not None
        entry.value = "changed goal"
        await session.commit()
    stale = await client.post(f"/api/scenarios/catalog/{SCENARIO_NAME}/estimate", json=body)
    assert stale.status_code == 409
    assert stale.json()["detail"].startswith("stale:")


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("objective_count", [0, 2])
async def test_launch_returns_unsupported_instead_of_a_server_error(
    client: AsyncClient, sqlite_instance: MemoryInterface, objective_count: int
) -> None:
    group = uuid4()
    secret = f"SECRET_ROUTE_{objective_count}_2757"
    seeds = [
        SeedObjective(value=f"goal {index}", dataset_name=DATASET, prompt_group_id=group, added_by="test")
        for index in range(objective_count)
    ]
    seeds.append(
        SeedPrompt(value=secret, dataset_name=DATASET, prompt_group_id=group, sequence=1, role="user", added_by="test")
    )
    await _store(sqlite_instance, *seeds)
    found = await sqlite_instance.get_seed_examples_by_ids_async(dataset_name=DATASET, example_ids=[group])
    body = {
        "example_selection": {
            "examples": [
                {
                    "selection_key": NAMED,
                    "example_id": str(group),
                    "fingerprint": dataset_example_fingerprint(
                        selection_key=NAMED, example_id=group, members=found[group]
                    ),
                }
            ]
        }
    }
    estimate = await client.post(f"/api/scenarios/catalog/{SCENARIO_NAME}/estimate", json=body)
    launch = await client.post(
        "/api/scenarios/runs",
        json={**body, "scenario_name": SCENARIO_NAME, "target_name": TARGET_NAME},
    )

    assert estimate.status_code == 200
    assert estimate.json()["dataset_size"] == {"kind": "bounded", "value": 1}
    assert launch.status_code == 400
    detail = launch.json()["detail"]
    assert detail.startswith("unsupported:")
    assert str(group) in detail
    assert secret not in detail
    assert _RouteScenario.prepared_counts == []


@pytest.mark.usefixtures("patch_central_database")
async def test_oversized_selection_is_rejected_before_resolution(client: AsyncClient) -> None:
    examples = [{"selection_key": NAMED, "example_id": str(uuid4()), "fingerprint": "ab" * 32} for _ in range(101)]
    with patch(
        "pyrit.backend.services.scenario_configuration_resolver.ScenarioConfigurationResolver.resolve_configuration_async",
        new_callable=AsyncMock,
        side_effect=AssertionError("resolver"),
    ):
        response = await client.post(
            f"/api/scenarios/catalog/{SCENARIO_NAME}/estimate",
            json={"example_selection": {"examples": examples}},
        )
    assert response.status_code == 422
