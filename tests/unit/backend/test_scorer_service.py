# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scorer backend service."""

import asyncio
import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyrit.backend.models import CreateScorerRequest
from pyrit.backend.services.chat_scoring_service import ChatScoringService
from pyrit.backend.services.saved_entries import NotSaveableError, SavedEntryConflictError, SavedEntryStorageError
from pyrit.backend.services.scorer_service import ScorerService, get_scorer_service
from pyrit.backend.services.service_lifecycle import close_services_async
from pyrit.common import REQUIRED_VALUE, apply_defaults, reset_default_values, set_default_value
from pyrit.models import (
    ComponentIdentifier,
    Scorable,
    Score,
    ScorerEvaluationIdentifier,
    ScorerIdentifier,
    ScoringExpectation,
    TargetIdentifier,
)
from pyrit.models.catalog import ComponentRecipe, RegistryEntryOrigin, RegistryRecipeKind, SavedEntryOwner
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.prompt_target import TextTarget
from pyrit.registry import DefaultInstanceRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.instance_registry import (
    SAVED_ENTRY_METADATA_KEY,
    SAVED_ENTRY_OWNER_METADATA_KEY,
    registration_origin,
)
from pyrit.registry.registry_recipe_restore import RegistryRecipeRestorer
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage
from pyrit.score import (
    ManualScorer,
    MessageToolCallScorer,
    ObjectiveScorerMetrics,
    ObservationSource,
    OtelToolCallScorer,
    Scorer,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)


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


async def test_list_scorers_rejects_a_cursor_that_names_no_instance() -> None:
    """A stale cursor must raise, not silently restart at the first page."""
    registry = ScorerRegistry.get_registry_singleton()
    registry.instances.register(SubStringScorer(substring="expected"), name="judge")
    service = ScorerService()

    with pytest.raises(ValueError, match="cursor is not valid"):
        await service.list_scorers_async(cursor="removed-scorer", limit=10)


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


@pytest.mark.parametrize("cursor", [None, "a-first", "z-last"])
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


async def test_decision_scorer_unknown_parameter_does_not_list_its_question_path(chat_target: str) -> None:
    with pytest.raises(ValueError, match="Unknown parameter 'nope' for 'DecisionScorer'") as error:
        await ScorerService().create_scorer_async(
            request=CreateScorerRequest(
                name="decision", type="DecisionScorer", params={"chat_target": chat_target, "nope": 1}
            )
        )

    assert "'chat_target'" in str(error.value)
    assert "decision_question_path" not in str(error.value)


def _persist_scorer(name: str, scorer_type: str, params: dict[str, Any]) -> CreateScorerRequest:
    return CreateScorerRequest(name=name, type=scorer_type, params=params, persist=True)


def _saved_scorer(source: Path, name: str) -> dict[str, Any]:
    return json.loads((source / "scorers" / f"{name}.json").read_text())


_LEAK = {"substring": "secret", "categories": ["leak"]}


async def test_saves_a_scorer_that_restore_rebuilds(tmp_path: Path) -> None:
    service = ScorerService()
    owner = SavedEntryOwner(oid="00000000-0000-0000-0000-000000000001", name="Ada")

    scorer = await service.create_scorer_async(
        request=_persist_scorer("leak_substring", "SubStringScorer", _LEAK), owner=owner, recipes_source=str(tmp_path)
    )

    assert scorer.origin is RegistryEntryOrigin.SAVED
    assert _saved_scorer(tmp_path, "leak_substring") == {
        "schema_version": 1,
        "class_module": "pyrit.score.true_false.substring_scorer",
        "class_name": "SubStringScorer",
        "params": {"substring": "secret", "categories": ["leak"]},
        "owner": {"oid": owner.oid, "name": "Ada"},
    }
    entry = ScorerRegistry.get_registry_singleton().instances.get_entry("leak_substring")
    assert entry is not None
    assert entry.metadata == {SAVED_ENTRY_METADATA_KEY: "scorers", SAVED_ENTRY_OWNER_METADATA_KEY: owner}
    saved_hash = entry.instance.get_identifier().hash
    ScorerRegistry.reset_registry_singleton()
    assert RegistryRecipeRestorer(source=str(tmp_path)).restore().failures == []
    restored = ScorerRegistry.get_registry_singleton().instances.get("leak_substring")
    assert restored is not None
    assert restored.get_identifier().hash == saved_hash


async def test_saves_a_scorer_only_over_a_target_that_comes_back_after_a_restart(
    chat_target: str, tmp_path: Path
) -> None:
    service = ScorerService()
    request = _persist_scorer("decision", "DecisionScorer", {"chat_target": chat_target})

    with pytest.raises(NotSaveableError) as refusal:
        await service.create_scorer_async(request=request, recipes_source=str(tmp_path))
    # The startup scripts register the same target again.
    with registration_origin(RegistryEntryOrigin.CONFIGURATION):
        TargetRegistry.get_registry_singleton().instances.register(TextTarget(), name=chat_target, replace=True)
    saved = await service.create_scorer_async(request=request, recipes_source=str(tmp_path))

    assert str(refusal.value) == (
        "Uses temporary target 'chat', which won't exist after a restart. It can still be created without saving it."
    )
    assert saved.origin is RegistryEntryOrigin.SAVED
    assert _saved_scorer(tmp_path, "decision")["params"] == {"chat_target": "chat"}


async def test_refuses_a_scorer_that_would_use_this_servers_key(tmp_path: Path) -> None:
    service = ScorerService()
    request = _persist_scorer(
        "content_safety", "AzureContentFilterScorer", {"endpoint": "https://contoso.cognitiveservices.azure.com/"}
    )

    with (
        patch.dict("os.environ", {"AZURE_CONTENT_SAFETY_API_KEY": "server-safety-key"}),
        pytest.raises(NotSaveableError) as refusal,
    ):
        await service.create_scorer_async(request=request, recipes_source=str(tmp_path))

    assert str(refusal.value) == (
        "'api_key' is empty, so 'AzureContentFilterScorer' can use this server's credential instead. "
        "It can still be created without saving it."
    )
    assert ScorerRegistry.get_registry_singleton().instances.get("content_safety") is None
    assert not (tmp_path / "scorers").exists()


async def test_a_taken_scorer_name_is_a_conflict_only_when_saving(tmp_path: Path) -> None:
    service = ScorerService()
    await service.create_scorer_async(request=CreateScorerRequest(name="leak", type="SubStringScorer", params=_LEAK))

    with pytest.raises(SavedEntryConflictError, match="already"):
        await service.create_scorer_async(
            request=_persist_scorer("leak", "SubStringScorer", _LEAK), recipes_source=str(tmp_path)
        )

    assert not (tmp_path / "scorers").exists()


async def test_a_saved_scorer_file_with_the_name_is_a_conflict_that_leaves_the_file(tmp_path: Path) -> None:
    storage = RegistryRecipeStorage(kind=RegistryRecipeKind.SCORERS, source=str(tmp_path))
    storage.create_recipe(name="leak", recipe=ComponentRecipe(class_module="m", class_name="X"))
    before = (tmp_path / "scorers" / "leak.json").read_bytes()

    with pytest.raises(SavedEntryConflictError, match="A saved scorer named 'leak' already exists."):
        await ScorerService().create_scorer_async(
            request=_persist_scorer("leak", "SubStringScorer", _LEAK), recipes_source=str(tmp_path)
        )

    assert ScorerRegistry.get_registry_singleton().instances.get("leak") is None
    assert (tmp_path / "scorers" / "leak.json").read_bytes() == before


async def test_reports_scorer_storage_failures_as_unavailable(tmp_path: Path) -> None:
    with (
        patch.object(RegistryRecipeStorage, "_save_local_conditional", side_effect=OSError("disk full")),
        pytest.raises(SavedEntryStorageError, match="Saved scorers can't be written right now."),
    ):
        await ScorerService().create_scorer_async(
            request=_persist_scorer("leak", "SubStringScorer", _LEAK), recipes_source=str(tmp_path)
        )

    assert ScorerRegistry.get_registry_singleton().instances.get("leak") is None


async def test_deletes_the_saved_file_when_the_scorer_cant_be_registered(tmp_path: Path) -> None:
    registry = ScorerRegistry.get_registry_singleton()

    with (
        patch.object(registry.instances, "register", side_effect=RuntimeError("x")),
        pytest.raises(RuntimeError, match="x"),
    ):
        await ScorerService().create_scorer_async(
            request=_persist_scorer("leak", "SubStringScorer", _LEAK), recipes_source=str(tmp_path)
        )

    assert not (tmp_path / "scorers" / "leak.json").exists()


async def test_list_get_and_create_tell_where_each_scorer_comes_from(tmp_path: Path) -> None:
    service = ScorerService()
    instances = ScorerRegistry.get_registry_singleton().instances
    with registration_origin(RegistryEntryOrigin.CONFIGURATION):
        instances.register(SubStringScorer(substring="startup"), name="a_startup")
    saved = await service.create_scorer_async(
        request=_persist_scorer("b_saved", "SubStringScorer", _LEAK), recipes_source=str(tmp_path)
    )
    temporary = await service.create_scorer_async(
        request=CreateScorerRequest(name="c_temporary", type="SubStringScorer", params=_LEAK)
    )

    first_page = await service.list_scorers_async(limit=2)
    second_page = await service.list_scorers_async(limit=2, cursor=first_page.pagination.next_cursor)
    fetched = [
        await service.get_scorer_async(scorer_registry_name=name) for name in ("a_startup", "b_saved", "c_temporary")
    ]

    expected = {
        "a_startup": RegistryEntryOrigin.CONFIGURATION,
        "b_saved": RegistryEntryOrigin.SAVED,
        "c_temporary": RegistryEntryOrigin.TEMPORARY,
    }
    listed = [*first_page.items, *second_page.items]
    assert {item.scorer_registry_name: item.origin for item in listed} == expected
    assert {item.scorer_registry_name: item.origin for item in fetched if item is not None} == expected
    assert (saved.origin, temporary.origin) == (RegistryEntryOrigin.SAVED, RegistryEntryOrigin.TEMPORARY)


async def test_a_reserved_scorer_name_is_refused_as_invalid_not_as_taken(tmp_path: Path) -> None:
    service = ScorerService()

    with pytest.raises(ValueError, match="reserved") as saved:
        await service.create_scorer_async(
            request=_persist_scorer("types", "SubStringScorer", _LEAK), recipes_source=str(tmp_path)
        )
    with pytest.raises(ValueError, match="reserved"):
        await service.create_scorer_async(
            request=CreateScorerRequest(name="types", type="SubStringScorer", params=_LEAK)
        )

    assert not isinstance(saved.value, (SavedEntryConflictError, NotSaveableError))
    assert not (tmp_path / "scorers").exists()


async def test_saving_an_unknown_scorer_type_is_an_invalid_request(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Scorer type 'NoSuchScorer' not found") as refusal:
        await ScorerService().create_scorer_async(
            request=_persist_scorer("missing", "NoSuchScorer", {}), recipes_source=str(tmp_path)
        )

    assert not isinstance(refusal.value, NotSaveableError)


async def test_a_scorer_save_finds_and_builds_the_scorer_off_the_event_loop(tmp_path: Path) -> None:
    registry = ScorerRegistry.get_registry_singleton()
    loop_thread = threading.current_thread()
    threads: list[str] = []
    contains, find, build = (
        ScorerRegistry.__contains__,
        registry.get_class,
        registry.create_instance_from_external_input,
    )

    def contains_noting_thread(self: ScorerRegistry, name: str) -> bool:
        threads.append("loop" if threading.current_thread() is loop_thread else "worker")
        return contains(self, name)

    def find_noting_thread(*args: Any, **kwargs: Any) -> Any:
        threads.append("loop" if threading.current_thread() is loop_thread else "worker")
        return find(*args, **kwargs)

    def build_noting_thread(*args: Any, **kwargs: Any) -> Any:
        threads.append("loop" if threading.current_thread() is loop_thread else "worker")
        return build(*args, **kwargs)

    with (
        patch.object(ScorerRegistry, "__contains__", contains_noting_thread),
        patch.object(registry, "get_class", side_effect=find_noting_thread),
        patch.object(registry, "create_instance_from_external_input", side_effect=build_noting_thread),
    ):
        await ScorerService().create_scorer_async(
            request=_persist_scorer("leak", "SubStringScorer", _LEAK), recipes_source=str(tmp_path)
        )

    assert len(threads) >= 3
    assert set(threads) == {"worker"}


def _register_default_composite_scorer(default: list[Any] | tuple[Any, ...]) -> None:
    """Register a composite scorer class whose scorers come from a configured default."""

    class DefaultCompositeScorer(TrueFalseCompositeScorer):
        @apply_defaults
        def __init__(self, *, scorers: list[TrueFalseScorer] = REQUIRED_VALUE) -> None:
            super().__init__(aggregator=TrueFalseScoreAggregator.AND, scorers=scorers)

    ScorerRegistry.get_registry_singleton().register_class(DefaultCompositeScorer)
    set_default_value(class_type=DefaultCompositeScorer, parameter_name="scorers", value=default)


@pytest.fixture
def startup_and_launch_scorers() -> Iterator[tuple[SubStringScorer, SubStringScorer, SubStringScorer]]:
    scorers = ScorerRegistry.get_registry_singleton().instances
    startup, other_startup = SubStringScorer(substring="startup"), SubStringScorer(substring="other")
    launch = SubStringScorer(substring="launch")
    with registration_origin(RegistryEntryOrigin.CONFIGURATION):
        scorers.register(startup, name="startup_substring")
        scorers.register(other_startup, name="other_startup_substring")
    scorers.register(launch, name="launch_substring")
    yield startup, other_startup, launch
    reset_default_values()


@pytest.mark.parametrize("container", [list, tuple])
async def test_a_saved_scorer_names_each_scorer_in_a_default_list(
    tmp_path: Path,
    startup_and_launch_scorers: tuple[SubStringScorer, SubStringScorer, SubStringScorer],
    container: Callable[[list[Any]], list[Any] | tuple[Any, ...]],
) -> None:
    startup, other_startup, _ = startup_and_launch_scorers
    _register_default_composite_scorer(default=container([startup, other_startup, startup]))

    await ScorerService().create_scorer_async(
        request=_persist_scorer("composite", "DefaultCompositeScorer", {}), recipes_source=str(tmp_path)
    )

    assert _saved_scorer(tmp_path, "composite")["params"] == {
        "scorers": ["startup_substring", "other_startup_substring", "startup_substring"]
    }


async def test_a_default_list_with_a_temporary_scorer_is_not_saved(
    tmp_path: Path, startup_and_launch_scorers: tuple[SubStringScorer, SubStringScorer, SubStringScorer]
) -> None:
    startup, _, launch = startup_and_launch_scorers
    _register_default_composite_scorer(default=[startup, launch])

    with pytest.raises(NotSaveableError) as refusal:
        await ScorerService().create_scorer_async(
            request=_persist_scorer("composite", "DefaultCompositeScorer", {}), recipes_source=str(tmp_path)
        )

    assert str(refusal.value) == (
        "Uses temporary scorer 'launch_substring', which won't exist after a restart. "
        "It can still be created without saving it."
    )
    assert not (tmp_path / "scorers").exists()
