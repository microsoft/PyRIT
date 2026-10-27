# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scorer backend service."""

import asyncio
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrit.backend.models.scorers import CreateScorerRequest
from pyrit.backend.services.chat_scoring_service import ChatScoringService
from pyrit.backend.services.scorer_service import ScorerService, get_scorer_service
from pyrit.backend.services.service_lifecycle import close_services_async
from pyrit.models import (
    ComponentIdentifier,
    Scorable,
    Score,
    ScorerEvaluationIdentifier,
    ScorerIdentifier,
    ScoringExpectation,
    TargetIdentifier,
)
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.prompt_target import TextTarget
from pyrit.registry import ScorerRegistry, TargetRegistry
from pyrit.registry.instance_registry import DefaultInstanceRegistry
from pyrit.score import (
    ManualScorer,
    MessageToolCallScorer,
    OtelToolCallScorer,
    SubStringScorer,
    TrueFalseInverterScorer,
)
from pyrit.score.observation.observation_source import ObservationSource
from pyrit.score.scorer import Scorer
from pyrit.score.scorer_evaluation.scorer_metrics import ObjectiveScorerMetrics


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


class _FileConfiguredScorer(_ServiceScorer):
    """A scorer with one plain external parameter and two that name files on the server."""

    def __init__(
        self, *, label: str = "service", data_file: Path | None = None, question_path: str | Path | None = None
    ) -> None:
        super().__init__(label=label)
        self.data_file = data_file
        self.question_path = question_path


class _FileRequiredScorer(_ServiceScorer):
    """A scorer that cannot be built without a file on the server."""

    def __init__(self, *, data_file: Path) -> None:
        super().__init__()
        self.data_file = data_file


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


async def test_chat_catalog_checks_response_contract_and_full_identity() -> None:
    registry = ScorerRegistry.get_registry_singleton()
    scorer = SubStringScorer(substring="expected")
    registry.instances.register(scorer, name="judge")
    registry.instances.register(TrueFalseInverterScorer(scorer=scorer), name="wrapped-judge")
    registry.instances.register(_ServiceScorer(), name="unsupported")
    service = ScorerService()
    catalog = await service.list_scorers_async()
    entries = {entry.scorer_registry_name: entry for entry in catalog.items}
    assert entries["judge"].chat_scoring_disabled_reason is None
    assert entries["wrapped-judge"].chat_scoring_disabled_reason is None
    assert entries["unsupported"].chat_scoring_disabled_reason
    reference = RegisteredComponentReference(registry_name="judge", identifier_hash=scorer.get_identifier().hash)
    assert ChatScoringService().resolve(reference) is scorer
    with pytest.raises(ValueError, match="configuration changed"):
        ChatScoringService().resolve(reference.model_copy(update={"identifier_hash": "different"}))
    assert entries["wrapped-judge"].display_type == "TrueFalseInverterScorer(SubStringScorer)"
    assert entries["wrapped-judge"].scorable_types == ["content", "content_entry", "message"]
    assert entries["unsupported"].scorable_types == []


async def test_chat_eligibility_uses_declared_message_support_not_message_family_inheritance() -> None:
    scorer = ManualScorer(value=True, rationale="Configured criterion", user_identifier="test")
    registry = ScorerRegistry.get_registry_singleton()
    registry.instances.register(scorer, name="declared-message-scorer")
    assert ChatScoringService.disabled_reason(scorer) is None
    reference = RegisteredComponentReference(
        registry_name="declared-message-scorer", identifier_hash=scorer.get_identifier().hash
    )
    assert ChatScoringService().resolve(reference) is scorer


async def test_inherited_scorer_lookup_uses_only_existing_exact_registry_instances() -> None:
    scorer = SubStringScorer(substring="scan configuration")
    service = ScorerService()
    reference = RegisteredComponentReference(
        registry_name="a different saved alias", identifier_hash=scorer.get_identifier().hash
    )
    registry = ScorerRegistry.get_registry_singleton()
    assert service.find_registered_scorer(reference.identifier_hash) is None
    assert registry.instances.get_all_instances() == []
    registry.instances.register(SubStringScorer(substring="different configuration"), name=reference.registry_name)
    assert service.find_registered_scorer(reference.identifier_hash) is None
    registry.instances.register(scorer, name="current-alias")
    assert service.find_registered_scorer(reference.identifier_hash) is scorer
    assert len(registry.instances.get_all_instances()) == 2


async def test_registry_projects_evaluation_identity_and_root_metrics() -> None:
    scorer = SubStringScorer(substring="expected")
    identifier = ScorerIdentifier(
        class_name="Wrapper",
        class_module="pyrit.score",
        sub_scorers=[
            ScorerIdentifier(
                class_name="Inner",
                class_module="pyrit.score",
                prompt_target=TargetIdentifier(
                    class_name="OpenAIChatTarget",
                    class_module="pyrit.prompt_target",
                    params={
                        "endpoint": "https://deployment.invalid",
                        "model_name": "logical-model",
                        "temperature": 0.7,
                        "max_requests_per_minute": 30,
                    },
                ),
            )
        ],
    )
    metrics = ObjectiveScorerMetrics(
        num_responses=12,
        num_human_raters=2,
        accuracy=0.9,
        accuracy_standard_error=0.02,
        f1_score=float("nan"),
        precision=0.95,
        recall=0.85,
        average_score_time_seconds=0.25,
    )
    ScorerRegistry.get_registry_singleton().instances.register(scorer, name="evaluated")
    with (
        patch.object(scorer, "get_identifier", return_value=identifier),
        patch.object(scorer, "get_scorer_metrics", return_value=metrics),
    ):
        instance = await ScorerService().get_scorer_async(scorer_registry_name="evaluated")
    assert instance is not None and instance.evaluation_identifier is not None
    assert instance.evaluation_identifier.eval_hash == ScorerEvaluationIdentifier(identifier).eval_hash
    projected = instance.evaluation_identifier.get_child_list("sub_scorers")[0].get_child("prompt_target")
    assert projected is not None and instance.metrics is not None
    assert projected.params == {"underlying_model_name": "logical-model", "temperature": 0.7}
    assert instance.display_type == "Wrapper(Inner)"
    assert instance.metrics["precision"] == 0.95 and instance.metrics["recall"] == 0.85
    assert instance.metrics["average_score_time_seconds"] == 0.25
    assert instance.metrics["f1_score"] is None
    assert "trial_scores" not in instance.metrics
    assert "deployment.invalid" not in instance.evaluation_identifier.model_dump_json()


async def test_registry_distinguishes_message_tool_calls_and_does_not_inherit_metrics() -> None:
    scorer = SubStringScorer(substring="expected")
    wrapper = TrueFalseInverterScorer(scorer=scorer)
    registry = ScorerRegistry.get_registry_singleton()
    registry.instances.register(wrapper, name="wrapper")
    registry.instances.register(MessageToolCallScorer(), name="tools")
    source = MagicMock(spec=ObservationSource)
    source.get_identifier.return_value = ComponentIdentifier(class_name="ToolSource", class_module="tests")
    registry.instances.register(OtelToolCallScorer(source=source), name="trace-tools")
    with patch.object(scorer, "get_scorer_metrics", side_effect=AssertionError("Do not use a child's metrics")):
        catalog = await ScorerService().list_scorers_async()
    entries = {entry.scorer_registry_name: entry for entry in catalog.items}
    assert entries["wrapper"].metrics is None
    assert entries["tools"].scorable_types == ["message"]
    assert entries["tools"].condition_types == ["ToolsCalled"]
    assert entries["trace-tools"].scorable_types == ["message", "trace"]
    source.acquire_async.assert_not_called()


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


@pytest.fixture
def chat_target() -> Iterator[str]:
    TargetRegistry.reset_registry_singleton()
    TargetRegistry.get_registry_singleton().instances.register(TextTarget(), name="chat")
    yield "chat"
    TargetRegistry.reset_registry_singleton()


async def test_types_leave_out_server_file_parameters() -> None:
    ScorerRegistry.get_registry_singleton().register_class(_FileConfiguredScorer)

    types = await ScorerService().list_scorer_types_async()

    entry = next(item for item in types.items if item.scorer_type == "_FileConfiguredScorer")
    assert [parameter.name for parameter in entry.parameters] == ["label"]


async def test_types_leave_out_scorers_that_require_a_server_file() -> None:
    registry = ScorerRegistry.get_registry_singleton()
    registry.register_class(_FileConfiguredScorer)
    registry.register_class(_FileRequiredScorer)

    types = await ScorerService().list_scorer_types_async()

    scorer_types = {item.scorer_type for item in types.items}
    assert "_FileConfiguredScorer" in scorer_types
    assert "_FileRequiredScorer" not in scorer_types


@pytest.mark.parametrize("register", [True, False])
@pytest.mark.parametrize("parameter_name", ["data_file", "question_path"])
async def test_create_scorer_rejects_server_file_parameters_before_construction(
    parameter_name: str, register: bool
) -> None:
    ScorerRegistry.get_registry_singleton().register_class(_FileConfiguredScorer)
    _FileConfiguredScorer.constructions = 0
    service = ScorerService()

    with pytest.raises(ValueError, match=f"'{parameter_name}' of '_FileConfiguredScorer' names a server file"):
        await service.create_scorer_async(
            request=CreateScorerRequest(
                name="file" if register else None,
                register=register,
                type="_FileConfiguredScorer",
                params={parameter_name: "/etc/hosts"},
            )
        )
    created = await service.create_scorer_async(
        request=CreateScorerRequest(name="labeled", type="_FileConfiguredScorer", params={"label": "custom"})
    )

    assert _FileConfiguredScorer.constructions == 1
    assert created.scorer_registry_name == "labeled"
    assert ScorerRegistry.get_registry_singleton().instances.get("file") is None


@pytest.mark.parametrize("register", [True, False])
async def test_decision_scorer_question_path_is_not_read_through_the_api(
    chat_target: str, tmp_path: Path, register: bool
) -> None:
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("api_secret: not-for-callers\n", encoding="utf-8")
    service = ScorerService()

    types = await service.list_scorer_types_async()
    with (
        patch("pyrit.common.yaml_loadable.YamlLoadable.from_yaml_file") as read_yaml,
        pytest.raises(ValueError, match="'decision_question_path' of 'DecisionScorer' names a server file") as error,
    ):
        await service.create_scorer_async(
            request=CreateScorerRequest(
                name="decision" if register else None,
                register=register,
                type="DecisionScorer",
                params={"chat_target": chat_target, "decision_question_path": str(settings_file)},
            )
        )

    entry = next(item for item in types.items if item.scorer_type == "DecisionScorer")
    assert "decision_question_path" not in [parameter.name for parameter in entry.parameters]
    read_yaml.assert_not_called()
    assert "not-for-callers" not in str(error.value)
    assert ScorerRegistry.get_registry_singleton().instances.get("decision") is None


async def test_decision_scorer_builds_through_the_api_with_its_bundled_question(chat_target: str) -> None:
    created = await ScorerService().create_scorer_async(
        request=CreateScorerRequest(name="decision", type="DecisionScorer", params={"chat_target": chat_target})
    )

    assert created.scorer_registry_name == "decision"
    assert ScorerRegistry.get_registry_singleton().instances.get("decision") is not None
