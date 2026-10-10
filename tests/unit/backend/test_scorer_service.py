# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scorer backend service."""

import asyncio
import threading
from collections.abc import Callable, Iterator
from unittest.mock import MagicMock, patch

import pytest

from pyrit.backend.models.scorers import CreateScorerRequest
from pyrit.backend.services.scorer_service import ScorerService, get_scorer_service
from pyrit.backend.services.service_lifecycle import close_services_async
from pyrit.models import ComponentIdentifier, Scorable, Score, ScoringExpectation
from pyrit.registry import ScorerRegistry
from pyrit.registry.instance_registry import DefaultInstanceRegistry
from pyrit.score.scorer import Scorer


class _ServiceScorer(Scorer):
    """A real registered scorer with an observable constructor."""

    constructions = 0

    def __init__(self, *, label: str = "service") -> None:
        super().__init__()
        type(self).constructions += 1
        self.label = label

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier(params={"label": self.label})

    async def _score_scorable_async(self, *, scorable: Scorable, expectation: ScoringExpectation | None) -> list[Score]:
        raise AssertionError("listing scorer metadata must not score")

    def validate_return_scores(self, scores: list[Score]) -> None:
        return None

    def get_scorer_metrics(self):
        return None


class _ObjectConfiguredScorer(_ServiceScorer):
    """A scorer with one external parameter and one that takes a Python object."""

    def __init__(self, *, label: str = "service", tokenizer: Callable[[str], list[str]] | None = None) -> None:
        super().__init__(label=label)
        self.tokenizer = tokenizer


@pytest.fixture(autouse=True)
def reset_scorer_registry(patch_central_database: MagicMock) -> Iterator[None]:
    get_scorer_service.cache_clear()
    ScorerRegistry.reset_registry_singleton()
    _ServiceScorer.constructions = 0
    yield
    get_scorer_service.cache_clear()
    ScorerRegistry.reset_registry_singleton()


async def test_types_are_metadata_only_and_empty_instance_list_is_valid() -> None:
    registry = ScorerRegistry.get_registry_singleton()
    registry.register_class(_ServiceScorer)
    service = ScorerService()

    types = await service.list_scorer_types_async()
    instances = await service.list_scorers_async()
    missing = await service.get_scorer_async(scorer_registry_name="missing")

    service_type = next(item for item in types.items if item.scorer_type == "_ServiceScorer")
    assert any(parameter.name == "label" for parameter in service_type.parameters)
    assert _ServiceScorer.constructions == 0
    assert instances.items == []
    assert instances.pagination.has_more is False
    assert missing is None


async def test_list_scorers_uses_sorted_names_and_cursor_pages() -> None:
    registry = ScorerRegistry.get_registry_singleton()
    registry.register_class(_ServiceScorer)
    registry.create_named_instance(name="z-last", type_name="_ServiceScorer", params={})
    registry.create_named_instance(name="a-first", type_name="_ServiceScorer", params={})
    service = ScorerService()

    first = await service.list_scorers_async(limit=1)
    second = await service.list_scorers_async(limit=1, cursor=first.pagination.next_cursor)

    assert [item.scorer_registry_name for item in first.items] == ["a-first"]
    assert first.pagination.has_more is True
    assert first.pagination.next_cursor == "a-first"
    assert [item.scorer_registry_name for item in second.items] == ["z-last"]
    assert second.pagination.has_more is False
    assert second.pagination.next_cursor is None


@pytest.mark.parametrize("cursor", [None, "a-first", "missing", "z-last"])
async def test_list_scorers_projects_only_requested_page(cursor: str | None) -> None:
    registry = ScorerRegistry.get_registry_singleton()
    registry.register_class(_ServiceScorer)
    for name in ("z-last", "a-first", "m-middle"):
        registry.create_named_instance(name=name, type_name="_ServiceScorer", params={})
    service = ScorerService()
    with patch.object(service, "_build_instance", wraps=service._build_instance) as build:
        response = await service.list_scorers_async(limit=1, cursor=cursor)

    expected_names = [] if cursor == "z-last" else ["m-middle" if cursor == "a-first" else "a-first"]
    assert [item.scorer_registry_name for item in response.items] == expected_names
    assert [call.kwargs["name"] for call in build.call_args_list] == expected_names
    assert response.pagination.prev_cursor == cursor
    assert response.pagination.has_more is bool(expected_names)
    assert response.pagination.next_cursor == (expected_names[0] if expected_names else None)


async def test_concurrent_creates_across_services_do_not_replace_instance() -> None:
    registry = ScorerRegistry.get_registry_singleton()
    registry.register_class(_ServiceScorer)
    services = [ScorerService(), ScorerService()]
    ready = threading.Barrier(2, timeout=5)
    normalize_tags = DefaultInstanceRegistry._normalize_tags

    def prepare_registration(tags: dict[str, str] | list[str] | None = None) -> dict[str, str]:
        ready.wait()
        return normalize_tags(tags)

    with patch.object(registry.instances, "_normalize_tags", side_effect=prepare_registration):
        results = await asyncio.gather(
            *(
                service.create_scorer_async(
                    request=CreateScorerRequest(name="contended", type="_ServiceScorer", params={"label": label})
                )
                for service, label in zip(services, ("first", "second"), strict=True)
            ),
            return_exceptions=True,
        )

    successes = [result for result in results if not isinstance(result, BaseException)]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(successes) == len(failures) == 1
    assert isinstance(failures[0], ValueError)
    assert "already exists" in str(failures[0])
    registered = registry.instances.get("contended")
    assert registered is not None
    assert registered.get_identifier().hash == successes[0].identifier.hash


async def test_close_services_clears_scorer_cache_before_registry_replacement() -> None:
    old_registry = ScorerRegistry.get_registry_singleton()
    old_registry.register_class(_ServiceScorer)
    old_registry.create_named_instance(name="old", type_name="_ServiceScorer", params={})
    old_service = get_scorer_service()

    await close_services_async()

    assert get_scorer_service.cache_info().currsize == 0
    ScorerRegistry.reset_registry_singleton()
    new_registry = ScorerRegistry.get_registry_singleton()
    new_registry.register_class(_ServiceScorer)
    new_registry.create_named_instance(name="new", type_name="_ServiceScorer", params={})
    new_service = get_scorer_service()

    assert new_service is not old_service
    response = await new_service.list_scorers_async()
    assert [item.scorer_registry_name for item in response.items] == ["new"]
    assert await new_service.get_scorer_async(scorer_registry_name="old") is None
    await new_service.create_scorer_async(request=CreateScorerRequest(name="created", type="_ServiceScorer"))
    assert new_registry.instances.get("created") is not None
    assert old_registry.instances.get("created") is None


async def test_create_scorer_accepts_only_external_inputs() -> None:
    ScorerRegistry.get_registry_singleton().register_class(_ObjectConfiguredScorer)
    service = ScorerService()

    with pytest.raises(ValueError, match="'tokenizer' of '_ObjectConfiguredScorer' cannot be set through the API"):
        await service.create_scorer_async(
            request=CreateScorerRequest(name="tokenized", type="_ObjectConfiguredScorer", params={"tokenizer": "x"})
        )
    created = await service.create_scorer_async(
        request=CreateScorerRequest(name="labeled", type="_ObjectConfiguredScorer", params={"label": "custom"})
    )

    assert created.scorer_registry_name == "labeled"
    assert ScorerRegistry.get_registry_singleton().instances.get("tokenized") is None


async def test_types_list_only_external_inputs() -> None:
    registry = ScorerRegistry.get_registry_singleton()
    registry.register_class(_ObjectConfiguredScorer)

    types = await ScorerService().list_scorer_types_async()

    entry = next(item for item in types.items if item.scorer_type == "_ObjectConfiguredScorer")
    assert [parameter.name for parameter in entry.parameters] == ["label"]
    metadata = registry.get_registered_class_metadata("_ObjectConfiguredScorer")
    assert metadata is not None
    assert {parameter.name for parameter in metadata.parameters} == {"label", "tokenizer"}


async def test_types_describe_sequences_as_lists() -> None:
    types = await ScorerService().list_scorer_types_async()

    serialized = {
        (entry.scorer_type, parameter["name"]): (parameter["type_name"], parameter["is_list"], parameter["required"])
        for entry in types.items
        for parameter in entry.model_dump(mode="json")["parameters"]
    }
    assert serialized[("AgentThreatRulesScorer", "fields")] == ("list[str]", True, False)
    assert serialized[("InsecureCodeScorer", "harm_categories")] == ("list[str]", True, True)
    assert serialized[("InsecureCodeScorer", "system_prompt")] == ("str", False, True)
    metadata = ScorerRegistry.get_registry_singleton().get_registered_class_metadata("AgentThreatRulesScorer")
    assert metadata is not None
    assert next(parameter for parameter in metadata.parameters if parameter.name == "fields").type_name == (
        "collections.abc.Sequence[str]"
    )
