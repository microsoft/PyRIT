# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for scorer API routes."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.models import ComponentIdentifier, Scorable, Score, ScoringExpectation, TargetIdentifier
from pyrit.prompt_target import PromptTarget
from pyrit.registry import ScorerRegistry, TargetRegistry
from pyrit.score.scorer import Scorer


class _RouteLeafScorer(Scorer):
    """Small real scorer used to exercise registry construction over HTTP."""

    def __init__(self, *, label: str = "leaf") -> None:
        super().__init__()
        self.label = label

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(params={"label": self.label})

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return []

    def validate_return_scores(self, scores: list[Score]) -> None:
        return None

    def get_scorer_metrics(self):
        return None


class _RouteCompositeScorer(Scorer):
    """Scorer with target and child references for full-identity coverage."""

    def __init__(
        self,
        *,
        chat_target: PromptTarget | None = None,
        scorers: list[Scorer],
        label: str = "composite",
    ) -> None:
        super().__init__(chat_target=chat_target)
        self.chat_target = chat_target
        self.scorers = scorers
        self.label = label

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(
            params={"label": self.label},
            prompt_target=self.chat_target.get_identifier() if self.chat_target else None,
            sub_scorers=[scorer.get_identifier() for scorer in self.scorers],
        )

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        return []

    def validate_return_scores(self, scores: list[Score]) -> None:
        return None

    def get_scorer_metrics(self):
        return None


class _RouteFailingScorer(_RouteLeafScorer):
    """Constructor used to verify failed builds are not registered."""

    def __init__(self, *, label: str = "leaf") -> None:
        raise RuntimeError("intentional construction failure")


@pytest.fixture(autouse=True)
def reset_scorer_registries():
    """Reset registry state and the service cache between API tests."""
    get_scorer_service.cache_clear()
    ScorerRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    yield
    get_scorer_service.cache_clear()
    ScorerRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()


def _register_test_scorers() -> ScorerRegistry:
    registry = ScorerRegistry.get_registry_singleton()
    registry.register_class(_RouteLeafScorer)
    registry.register_class(_RouteCompositeScorer)
    registry.register_class(_RouteFailingScorer)
    return registry


def test_scorer_routes_are_registered(compatibility_headers: dict[str, str]) -> None:
    """The scorer API exposes type discovery, instance CRUD, and creation routes."""
    client = TestClient(app, headers=compatibility_headers)
    responses = [
        client.get("/api/scorers/types"),
        client.get("/api/scorers"),
        client.get("/api/scorers/missing"),
        client.post("/api/scorers", json={"name": "created", "type": "MissingScorer", "params": {}}),
    ]

    assert [response.status_code for response in responses] == [200, 200, 404, 400]


def test_create_list_and_get_scorer_preserves_nested_identifiers(compatibility_headers: dict[str, str]) -> None:
    """Successful creation resolves real registry references and returns complete identities."""
    registry = _register_test_scorers()
    target_registry = TargetRegistry.get_registry_singleton()
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = TargetIdentifier(
        class_name="MockPromptTarget",
        class_module="unit.mocks",
        params={"model_name": "test-model"},
    )
    target.secret = "test-secret-should-not-appear"
    target_registry.instances.register(target, name="route-target")
    registry.create_named_instance(name="route-child", type_name="_RouteLeafScorer", params={"label": "child"})
    client = TestClient(app, headers=compatibility_headers)

    created = client.post(
        "/api/scorers",
        json={
            "name": "route-parent",
            "type": "_RouteCompositeScorer",
            "params": {"chat_target": "route-target", "scorers": ["route-child"], "label": "parent"},
        },
    )

    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["scorer_registry_name"] == "route-parent"
    assert payload["identifier"]["class_name"] == "_RouteCompositeScorer"
    assert payload["identifier"]["children"]["sub_scorers"][0]["class_name"] == "_RouteLeafScorer"
    assert payload["identifier"]["children"]["prompt_target"]["class_name"] == "MockPromptTarget"
    assert payload["identifier"]["children"]["sub_scorers"][0]["label"] == "child"
    assert payload["identifier"]["children"]["prompt_target"]["model_name"] == "test-model"
    assert "test-secret-should-not-appear" not in created.text

    listed = client.get("/api/scorers")
    fetched = client.get("/api/scorers/route-parent")
    assert listed.status_code == 200
    assert fetched.status_code == 200
    listed_parent = next(item for item in listed.json()["items"] if item["scorer_registry_name"] == "route-parent")
    assert fetched.json()["identifier"] == payload["identifier"]
    assert listed_parent["identifier"] == payload["identifier"]
    assert {item["scorer_registry_name"] for item in listed.json()["items"]} == {"route-child", "route-parent"}


@pytest.mark.parametrize(
    ("name", "type_name", "params", "expected_status"),
    [
        ("bad-reference", "_RouteCompositeScorer", {"scorers": ["missing-child"]}, 400),
        ("bad-parameter", "_RouteLeafScorer", {"unknown": "value"}, 400),
        ("bad-constructor", "_RouteFailingScorer", {}, 500),
    ],
)
def test_failed_scorer_creation_never_registers_instance(
    compatibility_headers: dict[str, str],
    *,
    name: str,
    type_name: str,
    params: dict[str, Any],
    expected_status: int,
) -> None:
    """Reference resolution and constructor failures leave the registry unchanged."""
    registry = _register_test_scorers()
    client = TestClient(app, headers=compatibility_headers)

    response = client.post("/api/scorers", json={"name": name, "type": type_name, "params": params})

    assert response.status_code == expected_status
    assert registry.instances.get(name) is None
    assert client.get(f"/api/scorers/{name}").status_code == 404


def test_duplicate_name_returns_409_and_does_not_replace_instance(compatibility_headers: dict[str, str]) -> None:
    registry = _register_test_scorers()
    first = registry.create_named_instance(name="duplicate", type_name="_RouteLeafScorer", params={"label": "first"})
    client = TestClient(app, headers=compatibility_headers)

    response = client.post(
        "/api/scorers", json={"name": "duplicate", "type": "_RouteLeafScorer", "params": {"label": "second"}}
    )

    assert response.status_code == 409
    assert registry.instances.get("duplicate") is first


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/scorers", {"name": "invalid name", "type": "_RouteLeafScorer", "params": {}}),
        ("/api/scorers?limit=0", None),
        ("/api/scorers?limit=201", None),
    ],
)
def test_invalid_scorer_request_shapes_return_422(
    compatibility_headers: dict[str, str], *, path: str, body: dict[str, Any] | None
) -> None:
    _register_test_scorers()
    client = TestClient(app, headers=compatibility_headers)

    response = client.post(path, json=body) if body is not None else client.get(path)

    assert response.status_code == 422


def test_type_discovery_is_metadata_only_and_list_paginates_stably(compatibility_headers: dict[str, str]) -> None:
    registry = _register_test_scorers()
    registry.create_named_instance(name="z-last", type_name="_RouteLeafScorer", params={})
    registry.create_named_instance(name="a-first", type_name="_RouteLeafScorer", params={})
    client = TestClient(app, headers=compatibility_headers)

    types = client.get("/api/scorers/types")
    first_page = client.get("/api/scorers?limit=1")

    assert types.status_code == 200
    assert any(item["scorer_type"] == "_RouteCompositeScorer" for item in types.json()["items"])
    assert first_page.status_code == 200
    assert first_page.json()["items"][0]["scorer_registry_name"] == "a-first"
    assert first_page.json()["pagination"]["has_more"] is True
    second_page = client.get(f"/api/scorers?limit=1&cursor={first_page.json()['pagination']['next_cursor']}")
    assert second_page.json()["items"][0]["scorer_registry_name"] == "z-last"
