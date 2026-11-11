# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import json
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from pyrit.backend.models.techniques import CreateTechniqueRequest
from pyrit.converter import Base64Converter, ColloquialWordswapConverter
from pyrit.models import Parameter
from pyrit.models.catalog import (
    RegistryRecipeKind,
    RegistryRestoreReport,
    RestoreFailure,
    RestoreFailureReason,
    SavedEntryOwner,
    SavedEntryRef,
    StoredRecipe,
    TechniqueRecipe,
)
from pyrit.prompt_target import OpenAIChatTarget, RoundRobinTarget
from pyrit.registry import AttackRegistry, AttackTechniqueRegistry, ConverterRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.instance_registry import SAVED_ENTRY_METADATA_KEY, saved_entry_owner
from pyrit.registry.registry_recipe_restore import RecipeBuilder, RecipeRejectedError, RegistryRecipeRestorer
from pyrit.registry.registry_recipe_storage import MAX_FAILURE_MESSAGE_LENGTH, RecipeListing, RegistryRecipeStorage
from unit.mocks import MockPromptTarget

_OPENAI_MODULE = "pyrit.prompt_target.openai.openai_chat_target"
_IDENTITY_TARGET = {
    "class_module": _OPENAI_MODULE,
    "class_name": "OpenAIChatTarget",
    "auth_mode": "identity",
    "params": {
        "endpoint": "https://contoso.openai.azure.com/openai/v1",
        "headers": "{}",
        "model_name": "gpt-4o",
    },
}
_ROUND_ROBIN_MODULE = "pyrit.prompt_target.round_robin_target"
_TECHNIQUE = {
    "type": "RedTeamingAttack",
    "params": {"max_turns": 5},
    "description": "Red teaming with a formal tone",
    "tags": ["custom"],
    "adversarial_chat": "contoso_gpt4o",
    "adversarial_system_prompt": "You are a red teaming assistant.",
    "request_converters": ["tone_formal"],
    "response_converters": [],
}

_REGISTRIES = (TargetRegistry, ConverterRegistry, ScorerRegistry, AttackTechniqueRegistry)


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    for registry in _REGISTRIES:
        registry.reset_registry_singleton()
    yield
    for registry in _REGISTRIES:
        registry.reset_registry_singleton()


def _save(root: Path, kind: RegistryRecipeKind, name: str, document: dict[str, Any]) -> None:
    _save_text(root, kind, name, json.dumps(document))


def _save_text(root: Path, kind: RegistryRecipeKind, name: str, text: str) -> None:
    folder = root / kind.value
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(text)


def _round_robin(*targets: str) -> dict[str, Any]:
    return {"class_module": _ROUND_ROBIN_MODULE, "class_name": "RoundRobinTarget", "params": {"targets": list(targets)}}


def _failures_by_name(failures: list[RestoreFailure]) -> dict[str | None, RestoreFailure]:
    return {failure.name: failure for failure in failures}


class _OptionalKeyTarget(MockPromptTarget):
    def __init__(self, *, endpoint: str, api_key: str | None = None) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.api_key = api_key


class _WorkspaceTarget(MockPromptTarget):
    caller_path_parameters = frozenset({"working_directory"})

    def __init__(self, *, working_directory: str | None = None) -> None:
        super().__init__()
        self.working_directory = working_directory


_COMPONENT_REGISTRIES = {
    RegistryRecipeKind.TARGETS: TargetRegistry,
    RegistryRecipeKind.CONVERTERS: ConverterRegistry,
    RegistryRecipeKind.SCORERS: ScorerRegistry,
}


async def _build_async(stored: StoredRecipe) -> None:
    """Stand in for the backend's builder: build with the registry calls the API's create makes."""
    recipe = stored.recipe
    if isinstance(recipe, TechniqueRecipe):
        techniques = AttackTechniqueRegistry.get_registry_singleton()
        args = recipe.model_dump(exclude_unset=True, exclude={"schema_version", "type", "tags"})
        factory = techniques.create_factory(
            name=stored.ref.name, attack_type=recipe.type, technique_tags=recipe.tags, strict_json=True, **args
        )
        techniques.instances.register_runtime(factory)
        return
    registry = _COMPONENT_REGISTRIES[stored.ref.kind].get_registry_singleton()
    cls = registry.get_class(recipe.class_name)
    params = dict(recipe.params)
    allowed_path_params: frozenset[str] = frozenset()
    if stored.ref.kind is RegistryRecipeKind.TARGETS:
        if recipe.auth_mode is not None:
            params.update(cls.get_auth_mode_parameters(auth_mode=recipe.auth_mode))
        allowed_path_params = cls.caller_path_parameters
    registry.create_named_instance(
        name=stored.ref.name,
        type_name=recipe.class_name,
        params=params,
        external_input=True,
        allowed_path_params=allowed_path_params,
    )


async def _restore(root: Path, *, build_async: RecipeBuilder = _build_async) -> RegistryRestoreReport:
    return await RegistryRecipeRestorer(source=str(root)).restore_async(build_async=build_async)


def _raising_builder(errors: dict[str, Exception]) -> RecipeBuilder:
    """Return a builder that raises the given error for each named entry and builds the rest."""

    async def build_async(stored: StoredRecipe) -> None:
        if stored.ref.name in errors:
            raise errors[stored.ref.name]
        await _build_async(stored)

    return build_async


@pytest.mark.usefixtures("patch_central_database")
class TestRestoreOrderAndRebuild:
    async def test_restores_every_kind_after_the_entries_it_uses(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "formal_red_team", _TECHNIQUE)
        _save(
            tmp_path,
            RegistryRecipeKind.SCORERS,
            "refusal",
            {
                "class_module": "pyrit.score.true_false.self_ask_refusal_scorer",
                "class_name": "SelfAskRefusalScorer",
                "params": {"chat_target": "contoso_gpt4o"},
            },
        )
        _save(
            tmp_path,
            RegistryRecipeKind.CONVERTERS,
            "tone_formal",
            {
                "class_module": "pyrit.converter.tone_converter",
                "class_name": "ToneConverter",
                "params": {"converter_target": "contoso_gpt4o", "tone": "formal"},
            },
        )
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)

        report = await _restore(tmp_path)

        assert report.failures == []
        assert [(ref.kind, ref.name) for ref in report.restored] == [
            (RegistryRecipeKind.TARGETS, "contoso_gpt4o"),
            (RegistryRecipeKind.CONVERTERS, "tone_formal"),
            (RegistryRecipeKind.SCORERS, "refusal"),
            (RegistryRecipeKind.TECHNIQUES, "formal_red_team"),
        ]
        target = TargetRegistry.get_registry_singleton().instances.get("contoso_gpt4o")
        assert isinstance(target, OpenAIChatTarget)
        converter = ConverterRegistry.get_registry_singleton().instances.get("tone_formal")
        assert converter is not None
        assert converter._converter_target is target
        factory = AttackTechniqueRegistry.get_registry_singleton().instances.get("formal_red_team")
        assert factory is not None
        assert factory.technique_tags == ["custom"]

    async def test_marks_each_restored_entry_with_its_kind(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "formal_red_team", _TECHNIQUE)
        _save(
            tmp_path,
            RegistryRecipeKind.SCORERS,
            "refusal",
            {
                "class_module": "pyrit.score.true_false.self_ask_refusal_scorer",
                "class_name": "SelfAskRefusalScorer",
                "params": {"chat_target": "contoso_gpt4o"},
            },
        )
        _save(
            tmp_path,
            RegistryRecipeKind.CONVERTERS,
            "tone_formal",
            {
                "class_module": "pyrit.converter.tone_converter",
                "class_name": "ToneConverter",
                "params": {"converter_target": "contoso_gpt4o", "tone": "formal"},
            },
        )
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        ConverterRegistry.get_registry_singleton().instances.register(Base64Converter(), name="startup_encoder")

        report = await _restore(tmp_path)

        assert report.failures == []
        entries = {
            "contoso_gpt4o": TargetRegistry.get_registry_singleton().instances.get_entry("contoso_gpt4o"),
            "tone_formal": ConverterRegistry.get_registry_singleton().instances.get_entry("tone_formal"),
            "refusal": ScorerRegistry.get_registry_singleton().instances.get_entry("refusal"),
            "formal_red_team": AttackTechniqueRegistry.get_registry_singleton().instances.get_entry("formal_red_team"),
            "startup_encoder": ConverterRegistry.get_registry_singleton().instances.get_entry("startup_encoder"),
        }
        assert {name: entry.metadata.get(SAVED_ENTRY_METADATA_KEY) for name, entry in entries.items() if entry} == {
            "contoso_gpt4o": "targets",
            "tone_formal": "converters",
            "refusal": "scorers",
            "formal_red_team": "techniques",
            "startup_encoder": None,
        }

    async def test_keeps_who_saved_each_restored_entry(self, tmp_path: Path) -> None:
        ada = {"oid": "00000000-0000-0000-0000-00000000000a", "name": "Ada"}
        _save(
            tmp_path,
            RegistryRecipeKind.SCORERS,
            "refusal",
            {
                "class_module": "pyrit.score.true_false.self_ask_refusal_scorer",
                "class_name": "SelfAskRefusalScorer",
                "params": {"chat_target": "contoso_gpt4o"},
                "owner": ada,
            },
        )
        _save(
            tmp_path,
            RegistryRecipeKind.CONVERTERS,
            "tone_formal",
            {
                "class_module": "pyrit.converter.tone_converter",
                "class_name": "ToneConverter",
                "params": {"converter_target": "contoso_gpt4o", "tone": "formal"},
                "owner": ada,
            },
        )
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", {**_IDENTITY_TARGET, "owner": ada})
        _save(tmp_path, RegistryRecipeKind.TARGETS, "saved_without_sign_in", _IDENTITY_TARGET)

        report = await _restore(tmp_path)

        assert report.failures == []
        targets = TargetRegistry.get_registry_singleton().instances
        entries = {
            "contoso_gpt4o": targets.get_entry("contoso_gpt4o"),
            "saved_without_sign_in": targets.get_entry("saved_without_sign_in"),
            "tone_formal": ConverterRegistry.get_registry_singleton().instances.get_entry("tone_formal"),
            "refusal": ScorerRegistry.get_registry_singleton().instances.get_entry("refusal"),
        }
        owner = SavedEntryOwner(**ada)
        assert {name: saved_entry_owner(entry.metadata) for name, entry in entries.items() if entry} == {
            "contoso_gpt4o": owner,
            "saved_without_sign_in": None,
            "tone_formal": owner,
            "refusal": owner,
        }

    async def test_finds_a_startup_entry_by_its_exact_name(self, tmp_path: Path) -> None:
        registry = TargetRegistry.get_registry_singleton()
        # Each name has a different normalized form, so a lookup that normalized names would miss them.
        for name in ("cafe\u0301", "target\uff10"):
            startup_target = OpenAIChatTarget(
                endpoint="https://startup.openai.azure.com/openai/v1", model_name="gpt-4o", auth_mode="identity"
            )
            registry.instances.register(startup_target, name=name)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "a_pool", _round_robin("cafe\u0301", "target\uff10"))

        report = await _restore(tmp_path)

        assert report.failures == []
        assert isinstance(registry.instances.get("a_pool"), RoundRobinTarget)

    async def test_reads_without_building_and_restores_what_was_read(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        restorer = RegistryRecipeRestorer(source=str(tmp_path))

        saved = restorer.read()
        assert "contoso_gpt4o" not in TargetRegistry.get_registry_singleton().instances
        with patch.object(RegistryRecipeStorage, "list_recipes", side_effect=AssertionError("read again")):
            report = await restorer.restore_async(saved=saved, build_async=_build_async)

        assert report.failures == []
        assert [ref.name for ref in report.restored] == ["contoso_gpt4o"]

    async def test_reads_off_the_event_loop_when_not_given_what_was_read(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        list_recipes = RegistryRecipeStorage.list_recipes
        reading_threads: set[int] = set()

        def record_thread(storage: RegistryRecipeStorage) -> RecipeListing:
            reading_threads.add(threading.get_ident())
            return list_recipes(storage)

        with patch.object(RegistryRecipeStorage, "list_recipes", autospec=True, side_effect=record_thread):
            report = await _restore(tmp_path)

        assert [ref.name for ref in report.restored] == ["contoso_gpt4o"]
        assert reading_threads
        assert threading.get_ident() not in reading_threads

    async def test_orders_entries_of_the_same_kind(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "a_pool", _round_robin("b_member", "c_member"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "b_member", _IDENTITY_TARGET)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "c_member", _IDENTITY_TARGET)

        report = await _restore(tmp_path)

        assert report.failures == []
        assert [ref.name for ref in report.restored] == ["b_member", "c_member", "a_pool"]
        assert isinstance(TargetRegistry.get_registry_singleton().instances.get("a_pool"), RoundRobinTarget)

    def test_technique_recipe_matches_the_create_request(self) -> None:
        recipe_fields = set(TechniqueRecipe.model_fields) - {"schema_version"}
        request_fields = set(CreateTechniqueRequest.model_fields) - {"name"}
        request = CreateTechniqueRequest.model_validate({"name": "formal_red_team", **_TECHNIQUE})
        recipe = TechniqueRecipe.model_validate(_TECHNIQUE)

        assert recipe_fields == request_fields
        assert recipe.model_dump(exclude_unset=True, exclude={"schema_version", "type", "tags"}) == (
            request.model_dump(exclude_unset=True, exclude={"name", "type", "tags"})
        )

    def test_technique_recipe_defaults_match_the_create_request(self) -> None:
        recipe = TechniqueRecipe.model_validate({"type": "RedTeamingAttack"})
        request = CreateTechniqueRequest.model_validate({"name": "formal_red_team", "type": "RedTeamingAttack"})

        assert recipe.model_dump(exclude={"schema_version"}) == request.model_dump(exclude={"name"})

    @pytest.mark.parametrize(
        "changes",
        [
            {"tags": ["all"]},
            {"tags": ["Custom", "custom"]},
            {"tags": ["bad-tag"]},
            {"type": ""},
            {"params": {"x" * 300: 1}},
            {"request_converters": ["c"] * 101},
            {"params": {"temperature": float("nan")}},
            {"description": "x" * 100_001},
            {"unexpected": True},
        ],
    )
    def test_technique_recipe_rejects_what_the_create_request_rejects(self, changes: dict[str, Any]) -> None:
        document = {**_TECHNIQUE, **changes}

        with pytest.raises(ValidationError):
            CreateTechniqueRequest.model_validate({"name": "formal_red_team", **document})
        with pytest.raises(ValidationError):
            TechniqueRecipe.model_validate(document)


@pytest.mark.usefixtures("patch_central_database")
class TestRestoreFailures:
    async def test_a_technique_waits_for_its_saved_adversarial_target(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", {**_IDENTITY_TARGET, "class_name": "NoSuchTarget"})
        without_converters = {key: value for key, value in _TECHNIQUE.items() if key != "request_converters"}
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "formal_red_team", without_converters)
        startup_target = OpenAIChatTarget(
            endpoint="https://startup.openai.azure.com/openai/v1", model_name="gpt-4o", auth_mode="identity"
        )
        TargetRegistry.get_registry_singleton().instances.register(startup_target, name="contoso_gpt4o")

        report = await _restore(tmp_path)

        failure = _failures_by_name(report.failures)["formal_red_team"]
        assert failure.reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert failure.dependency == SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="contoso_gpt4o")

    @pytest.mark.parametrize("field", ["request_converters", "response_converters"])
    async def test_a_technique_waits_for_a_saved_converter_in_either_list(self, tmp_path: Path, field: str) -> None:
        ConverterRegistry.get_registry_singleton().instances.register(Base64Converter(), name="shared_name")
        _save_text(tmp_path, RegistryRecipeKind.CONVERTERS, "shared_name", "{not json")
        _save(
            tmp_path, RegistryRecipeKind.TECHNIQUES, "uses_shared", {"type": "RedTeamingAttack", field: ["shared_name"]}
        )

        report = await _restore(tmp_path)

        failure = _failures_by_name(report.failures)["uses_shared"]
        assert failure.reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert failure.dependency == SavedEntryRef(kind=RegistryRecipeKind.CONVERTERS, name="shared_name")

    async def test_reports_an_unknown_attack_type(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "unknown_attack", {"type": "NoSuchAttack"})

        (failure,) = (await _restore(tmp_path)).failures

        assert (failure.name, failure.reason) == ("unknown_attack", RestoreFailureReason.UNKNOWN_CLASS)

    async def test_technique_parameters_are_checked_as_strict_json(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        without_converters = {key: value for key, value in _TECHNIQUE.items() if key != "request_converters"}
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "loose", {**without_converters, "params": {"max_turns": "5"}})

        report = await _restore(tmp_path)

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("loose", RestoreFailureReason.BUILD_ERROR)

    async def test_reports_dependency_loops_and_entries_waiting_on_them(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "loop_a", _round_robin("loop_b"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "loop_b", _round_robin("loop_a"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "waiting", _round_robin("loop_a"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "self_loop", _round_robin("self_loop"))

        report = await _restore(tmp_path)

        failures = _failures_by_name(report.failures)
        assert report.restored == []
        assert failures["loop_a"].reason is RestoreFailureReason.DEPENDENCY_LOOP
        assert failures["loop_b"].reason is RestoreFailureReason.DEPENDENCY_LOOP
        assert failures["self_loop"].reason is RestoreFailureReason.DEPENDENCY_LOOP
        assert failures["waiting"].reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert failures["waiting"].dependency == SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="loop_a")

    async def test_names_a_loop_member_as_the_reason_for_a_loop(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "a", _round_robin("c", "b"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "b", _round_robin("a"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "c", _round_robin("d"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "d", _round_robin("c"))

        report = await _restore(tmp_path)

        failures = _failures_by_name(report.failures)
        assert failures["a"].reason is RestoreFailureReason.DEPENDENCY_LOOP
        assert failures["a"].dependency == SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="b")

    async def test_reports_missing_and_failed_dependencies(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "broken", {**_IDENTITY_TARGET, "class_name": "NoSuchTarget"})
        _save(tmp_path, RegistryRecipeKind.TARGETS, "uses_broken", _round_robin("broken"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "uses_ghost", _round_robin("ghost"))
        without_adversarial = {key: value for key, value in _TECHNIQUE.items() if key != "adversarial_chat"}
        _save(
            tmp_path,
            RegistryRecipeKind.TECHNIQUES,
            "uses_unparsable",
            {**without_adversarial, "request_converters": ["bad"]},
        )
        _save_text(tmp_path, RegistryRecipeKind.CONVERTERS, "bad", "{not json")

        report = await _restore(tmp_path)

        failures = _failures_by_name(report.failures)
        assert failures["broken"].reason is RestoreFailureReason.UNKNOWN_CLASS
        assert failures["uses_broken"].reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert failures["uses_ghost"].reason is RestoreFailureReason.MISSING_DEPENDENCY
        assert failures["uses_ghost"].dependency == SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="ghost")
        assert failures["bad"].reason is RestoreFailureReason.INVALID_RECIPE
        assert failures["uses_unparsable"].reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert failures["uses_unparsable"].dependency == SavedEntryRef(kind=RegistryRecipeKind.CONVERTERS, name="bad")

    async def test_restores_a_class_that_moved_within_pyrit(self, tmp_path: Path) -> None:
        _save(
            tmp_path,
            RegistryRecipeKind.TARGETS,
            "contoso_gpt4o",
            {**_IDENTITY_TARGET, "class_module": "pyrit.prompt_target.openai_chat_target"},
        )

        report = await _restore(tmp_path)

        assert report.failures == []
        assert [ref.name for ref in report.restored] == ["contoso_gpt4o"]

    async def test_never_imports_a_module_named_by_a_file(self, tmp_path: Path) -> None:
        module = "pyrit_restore_probe"
        modules = tmp_path / "modules"
        modules.mkdir()
        (modules / f"{module}.py").write_text("class ProbeTarget:\n    pass\n")
        recipes = tmp_path / "recipes"
        _save(recipes, RegistryRecipeKind.TARGETS, "moved", {**_IDENTITY_TARGET, "class_module": module})
        _save(recipes, RegistryRecipeKind.TARGETS, "unknown", {"class_module": module, "class_name": "ProbeTarget"})

        with patch.object(sys, "path", [str(modules), *sys.path]), patch.dict(sys.modules):
            report = await _restore(recipes)
            imported = module in sys.modules

        failures = _failures_by_name(report.failures)
        assert failures["moved"].reason is RestoreFailureReason.MODULE_MISMATCH
        assert _OPENAI_MODULE in failures["moved"].message
        assert failures["unknown"].reason is RestoreFailureReason.UNKNOWN_CLASS
        assert not imported

    async def test_refuses_a_stored_credential(self, tmp_path: Path) -> None:
        stored_key = {**_IDENTITY_TARGET, "params": {**_IDENTITY_TARGET["params"], "api_key": "sk-secret-value"}}
        _save(tmp_path, RegistryRecipeKind.TARGETS, "stored_key", stored_key)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "good", _IDENTITY_TARGET)

        report = await _restore(tmp_path)

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("stored_key", RestoreFailureReason.UNSUPPORTED_CREDENTIAL)
        assert "sk-secret-value" not in failure.message
        assert [ref.name for ref in report.restored] == ["good"]

    @pytest.mark.parametrize(
        ("name", "left_out"),
        [("server_key", "auth_mode"), ("server_headers", "headers"), ("server_endpoint", "endpoint")],
    )
    async def test_refuses_a_target_that_would_use_this_servers_credentials(
        self, tmp_path: Path, name: str, left_out: str
    ) -> None:
        recipe = {key: value for key, value in _IDENTITY_TARGET.items() if key != left_out}
        recipe["params"] = {key: value for key, value in _IDENTITY_TARGET["params"].items() if key != left_out}
        _save(tmp_path, RegistryRecipeKind.TARGETS, name, recipe)
        server_settings = {
            "OPENAI_CHAT_KEY": "sk-server-key",
            "OPENAI_ADDITIONAL_REQUEST_HEADERS": '{"api-key": "sk-server-key"}',
            "OPENAI_CHAT_ENDPOINT": "https://operator:sk-server-key@contoso.openai.azure.com/openai/v1",
        }

        with patch.dict(os.environ, server_settings):
            report = await _restore(tmp_path)

        (failure,) = report.failures
        assert (failure.name, failure.reason) == (name, RestoreFailureReason.UNSUPPORTED_CREDENTIAL)
        assert f"'{left_out if left_out != 'auth_mode' else 'api_key'}' is empty" in failure.message
        assert "sk-server-key" not in failure.message
        assert name not in TargetRegistry.get_registry_singleton().instances.get_names()

    async def test_refuses_a_target_that_signs_in_with_this_servers_account(self, tmp_path: Path) -> None:
        _save(
            tmp_path,
            RegistryRecipeKind.TARGETS,
            "copilot",
            {"class_module": "pyrit.prompt_target.websocket_copilot_target", "class_name": "WebSocketCopilotTarget"},
        )

        report = await _restore(tmp_path)

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("copilot", RestoreFailureReason.UNSUPPORTED_CLASS)
        assert "Copilot account" in failure.message

    async def test_restores_a_class_whose_optional_credentials_are_left_empty(self, tmp_path: Path) -> None:
        TargetRegistry.get_registry_singleton().register_class(_OptionalKeyTarget)
        _save(
            tmp_path,
            RegistryRecipeKind.TARGETS,
            "anonymous_agent",
            {
                "class_module": _OptionalKeyTarget.__module__,
                "class_name": "_OptionalKeyTarget",
                "params": {"endpoint": "https://agent.example.test"},
            },
        )

        report = await _restore(tmp_path)

        assert report.failures == []
        target = TargetRegistry.get_registry_singleton().instances.get("anonymous_agent")
        assert isinstance(target, _OptionalKeyTarget)
        assert (target.endpoint, target.api_key) == ("https://agent.example.test", None)

    async def test_refuses_a_class_that_needs_the_server_upload_folder(self, tmp_path: Path) -> None:
        _save(
            tmp_path,
            RegistryRecipeKind.TARGETS,
            "uploader",
            {
                "class_module": "pyrit.prompt_target.http_target.httpx_api_target",
                "class_name": "HTTPXAPITarget",
                "params": {"http_url": "https://example.test/upload", "file_path": "/etc/hosts"},
            },
        )

        report = await _restore(tmp_path)

        (failure,) = report.failures
        assert failure.reason is RestoreFailureReason.UNSUPPORTED_CLASS
        assert "uploader" not in TargetRegistry.get_registry_singleton().instances.get_names()

    @pytest.mark.parametrize("auth_mode", [[], {}, ["identity"]])
    async def test_reports_malformed_values_without_stopping_the_restore(
        self, tmp_path: Path, auth_mode: object
    ) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "malformed", {**_IDENTITY_TARGET, "auth_mode": auth_mode})
        _save(tmp_path, RegistryRecipeKind.TARGETS, "good", _IDENTITY_TARGET)

        report = await _restore(tmp_path)

        assert [(failure.name, failure.reason) for failure in report.failures] == [
            ("malformed", RestoreFailureReason.INVALID_RECIPE)
        ]
        assert "auth_mode" in report.failures[0].message
        assert [ref.name for ref in report.restored] == ["good"]

    async def test_a_planning_bug_fails_only_that_entry_as_a_logged_build_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "a_first", _IDENTITY_TARGET)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "b_second", _IDENTITY_TARGET)
        registry = TargetRegistry.get_registry_singleton()
        get_metadata = registry.get_registered_class_metadata
        errors = iter([RuntimeError("bug")])

        def fail_once(name: str) -> Any:
            if (error := next(errors, None)) is not None:
                raise error
            return get_metadata(name)

        with (
            patch.object(registry, "get_registered_class_metadata", side_effect=fail_once),
            caplog.at_level("ERROR", logger="pyrit.registry.registry_recipe_restore"),
        ):
            report = await _restore(tmp_path)

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("a_first", RestoreFailureReason.BUILD_ERROR)
        assert failure.message == "RuntimeError: bug"
        assert [ref.name for ref in report.restored] == ["b_second"]
        assert "Traceback (most recent call last)" in caplog.text

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("got https://user:pass@host/v1?sig=SECRET#frag tail", "ValueError: got https://host/v1 tail"),
            (
                "top_p must be at most 100% for gpt4o@2024-08",
                "ValueError: top_p must be at most 100% for gpt4o@2024-08",
            ),
            ("bad key \n\x1b[31mFAKE", "ValueError: bad key \\n\\x1b[31mFAKE"),
        ],
        ids=["url", "plain", "control"],
    )
    async def test_a_build_error_is_reported_safely(self, tmp_path: Path, text: str, expected: str) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "broken", _IDENTITY_TARGET)

        report = await _restore(tmp_path, build_async=_raising_builder({"broken": ValueError(text)}))

        (failure,) = report.failures
        assert (failure.reason, failure.message) == (RestoreFailureReason.BUILD_ERROR, expected)

    async def test_a_long_build_error_is_shortened(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "broken", _IDENTITY_TARGET)

        report = await _restore(tmp_path, build_async=_raising_builder({"broken": ValueError("x" * 5_000)}))

        (failure,) = report.failures
        assert len(failure.message) == MAX_FAILURE_MESSAGE_LENGTH
        assert failure.message.endswith("...")

    async def test_a_build_error_that_cannot_be_printed_is_reported_by_its_type(self, tmp_path: Path) -> None:
        class UnprintableError(Exception):
            def __str__(self) -> str:
                raise RuntimeError("cannot print")

        _save(tmp_path, RegistryRecipeKind.TARGETS, "broken", _IDENTITY_TARGET)

        report = await _restore(tmp_path, build_async=_raising_builder({"broken": UnprintableError()}))

        (failure,) = report.failures
        assert (failure.reason, failure.message) == (RestoreFailureReason.BUILD_ERROR, "UnprintableError")

    async def test_logs_each_failure_once_with_its_traceback_and_without_url_credentials(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "leaky", _IDENTITY_TARGET)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "pool", _round_robin("leaky"))
        error = ValueError("could not reach https://host/v1?sig=SECRET")

        with caplog.at_level("WARNING", logger="pyrit.registry.registry_recipe_restore"):
            report = await _restore(tmp_path, build_async=_raising_builder({"leaky": error}))

        assert {failure.name for failure in report.failures} == {"leaky", "pool"}
        leaky_records = [record for record in caplog.records if "target 'leaky' (" in record.getMessage()]
        assert len(leaky_records) == 1
        assert "Traceback (most recent call last)" in leaky_records[0].getMessage()
        assert len([record for record in caplog.records if "target 'pool' (" in record.getMessage()]) == 1
        assert "SECRET" not in caplog.text

    async def test_logs_every_problem_with_a_file_that_is_not_valid_in_full(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        many_keys = [f"unknown_{index:03d}_{'x' * 40}" for index in range(100)]
        twins = ["p" * 70 + "_alpha", "p" * 70 + "_beta"]
        _save(tmp_path, RegistryRecipeKind.TARGETS, "many_keys", {**_IDENTITY_TARGET, **dict.fromkeys(many_keys, True)})
        _save(tmp_path, RegistryRecipeKind.TARGETS, "twins", {**_IDENTITY_TARGET, **dict.fromkeys(twins, True)})

        with caplog.at_level("ERROR", logger="pyrit.registry.registry_recipe_restore"):
            report = await _restore(tmp_path)

        messages = {failure.name: failure.message for failure in report.failures}
        assert len(messages["many_keys"]) == MAX_FAILURE_MESSAGE_LENGTH
        for name, keys in (("many_keys", many_keys), ("twins", twins)):
            (record,) = [record for record in caplog.records if f"target '{name}' (" in record.getMessage()]
            lines = record.getMessage().splitlines()
            assert all(f"  {key}: Extra inputs are not permitted" in lines for key in keys)

    async def test_logs_the_problems_of_a_file_escaped_and_without_stored_values_or_url_credentials(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        url_key = "https://user:pw-token@acct.blob.core.windows.net/c/x.json?sv=2024&sig=sas-token#frag-token"
        long_url_key = f"https://user:{'longpw' * 20}@host/path?sig=sas-token"
        document = {
            **_IDENTITY_TARGET,
            "schema_version": "STORED-VALUE",
            url_key: True,
            long_url_key: True,
            "\n\x1b[31mFAKE": True,
        }
        _save(tmp_path, RegistryRecipeKind.TARGETS, "unsafe", document)

        with caplog.at_level("ERROR", logger="pyrit.registry.registry_recipe_restore"):
            report = await _restore(tmp_path)

        (record,) = [record for record in caplog.records if "target 'unsafe' (" in record.getMessage()]
        lines = record.getMessage().splitlines()
        assert "  schema_version: Input should be a valid integer" in lines
        assert "  https://acct.blob.core.windows.net/c/x.json: Extra inputs are not permitted" in lines
        assert "  https://host/path: Extra inputs are not permitted" in lines
        assert "  \\n\\x1b[31mFAKE: Extra inputs are not permitted" in lines
        assert all(line.isprintable() for line in lines)
        for hidden in ("STORED-VALUE", "pw-token", "longpw", "sig=", "sas-token", "frag-token"):
            assert hidden not in caplog.text
            assert hidden not in report.model_dump_json()

    async def test_reports_a_reference_name_that_is_not_valid_text_as_invalid(self, tmp_path: Path) -> None:
        _save_text(
            tmp_path,
            RegistryRecipeKind.TARGETS,
            "pool",
            '{"class_module": "pyrit.prompt_target.round_robin_target", "class_name": "RoundRobinTarget", '
            '"params": {"targets": ["ghost\\ud800"]}}',
        )

        report = await _restore(tmp_path)

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("pool", RestoreFailureReason.INVALID_RECIPE)
        assert json.loads(report.model_dump_json())["failures"][0]["name"] == "pool"

    async def test_refuses_a_stored_credential_in_attack_parameters(self, tmp_path: Path) -> None:
        attack_registry = AttackRegistry.get_registry_singleton()
        original_get_registered_class_metadata = attack_registry.get_registered_class_metadata

        def with_credential(name: str) -> Any:
            metadata = original_get_registered_class_metadata(name)
            credential = Parameter(name="api_key", description="", sensitive=True)
            return dataclasses.replace(metadata, parameters=(*metadata.parameters, credential))

        _save(
            tmp_path,
            RegistryRecipeKind.TECHNIQUES,
            "keyed",
            {"type": "RedTeamingAttack", "params": {"api_key": "sk-x"}},
        )

        with patch.object(attack_registry, "get_registered_class_metadata", side_effect=with_credential):
            report = await _restore(tmp_path)

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("keyed", RestoreFailureReason.UNSUPPORTED_CREDENTIAL)
        assert "sk-x" not in failure.message

    async def test_an_empty_reference_is_left_unset(self, tmp_path: Path) -> None:
        _save(
            tmp_path,
            RegistryRecipeKind.CONVERTERS,
            "puzzled",
            {
                "class_module": "pyrit.converter.puzzled.puzzled_converter",
                "class_name": "PuzzledConverter",
                "params": {"converter_target": ""},
            },
        )

        report = await _restore(tmp_path)

        assert report.failures == []
        assert [ref.name for ref in report.restored] == ["puzzled"]

    async def test_follows_converter_and_scorer_references(self, tmp_path: Path) -> None:
        _save(
            tmp_path,
            RegistryRecipeKind.CONVERTERS,
            "selective",
            {
                "class_module": "pyrit.converter.selective_text_converter",
                "class_name": "SelectiveTextConverter",
                "params": {"sub_converter": "ghost_converter"},
            },
        )
        _save(
            tmp_path,
            RegistryRecipeKind.SCORERS,
            "composite",
            {
                "class_module": "pyrit.score.true_false.true_false_composite_scorer",
                "class_name": "TrueFalseCompositeScorer",
                "params": {"scorers": ["ghost_scorer"]},
            },
        )

        report = await _restore(tmp_path)

        failures = _failures_by_name(report.failures)
        assert failures["selective"].dependency == SavedEntryRef(
            kind=RegistryRecipeKind.CONVERTERS, name="ghost_converter"
        )
        assert failures["composite"].dependency == SavedEntryRef(kind=RegistryRecipeKind.SCORERS, name="ghost_scorer")
        assert {failure.reason for failure in report.failures} == {RestoreFailureReason.MISSING_DEPENDENCY}

    async def test_rejects_server_paths_the_way_the_api_does(self, tmp_path: Path) -> None:
        _save(
            tmp_path,
            RegistryRecipeKind.CONVERTERS,
            "wordswap",
            {
                "class_module": "pyrit.converter.colloquial_wordswap_converter",
                "class_name": "ColloquialWordswapConverter",
                "params": {"wordswap_path": "/etc/passwd"},
            },
        )

        report = await _restore(tmp_path)

        (failure,) = report.failures
        assert failure.reason is RestoreFailureReason.UNSUPPORTED_CLASS
        assert "wordswap_path" in failure.message
        assert "wordswap" not in ConverterRegistry.get_registry_singleton().instances

    async def test_lets_a_target_set_the_path_parameters_its_class_allows_callers(self, tmp_path: Path) -> None:
        TargetRegistry.get_registry_singleton().register_class(_WorkspaceTarget)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        recipes = tmp_path / "recipes"
        _save(
            recipes,
            RegistryRecipeKind.TARGETS,
            "workspace_agent",
            {
                "class_module": _WorkspaceTarget.__module__,
                "class_name": "_WorkspaceTarget",
                "params": {"working_directory": str(workspace)},
            },
        )

        report = await _restore(recipes)

        assert report.failures == []
        target = TargetRegistry.get_registry_singleton().instances.get("workspace_agent")
        assert isinstance(target, _WorkspaceTarget)
        assert target.working_directory == str(workspace)

    async def test_gives_only_targets_the_path_parameters_their_class_allows_callers(self, tmp_path: Path) -> None:
        class CallerPathWordswap(ColloquialWordswapConverter):
            caller_path_parameters = frozenset({"wordswap_path"})

        ConverterRegistry.get_registry_singleton().register_class(CallerPathWordswap)
        _save(
            tmp_path,
            RegistryRecipeKind.CONVERTERS,
            "wordswap",
            {
                "class_module": CallerPathWordswap.__module__,
                "class_name": "CallerPathWordswap",
                "params": {"wordswap_path": "/etc/passwd"},
            },
        )

        report = await _restore(tmp_path)

        (failure,) = report.failures
        assert failure.reason is RestoreFailureReason.UNSUPPORTED_CLASS
        assert "wordswap_path" in failure.message
        assert "wordswap" not in ConverterRegistry.get_registry_singleton().instances

    async def test_reports_a_name_taken_at_startup_and_fails_its_dependents(self, tmp_path: Path) -> None:
        startup_target = OpenAIChatTarget(
            endpoint="https://startup.openai.azure.com/openai/v1", model_name="gpt-4o", auth_mode="identity"
        )
        TargetRegistry.get_registry_singleton().instances.register(startup_target, name="contoso_gpt4o")
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "pool", _round_robin("contoso_gpt4o"))

        report = await _restore(tmp_path)

        failures = _failures_by_name(report.failures)
        assert failures["contoso_gpt4o"].reason is RestoreFailureReason.NAME_CLASH
        assert failures["pool"].reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert TargetRegistry.get_registry_singleton().instances.get("contoso_gpt4o") is startup_target

    async def test_reports_a_technique_name_that_matches_a_tag_in_another_case(self, tmp_path: Path) -> None:
        without_converters = {key: value for key, value in _TECHNIQUE.items() if key != "request_converters"}
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "formal_red_team", {**without_converters, "tags": ["Custom"]})
        assert (await _restore(tmp_path)).failures == []
        TargetRegistry.reset_registry_singleton()
        (tmp_path / "techniques" / "formal_red_team.json").unlink()
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "custom", {**without_converters, "tags": []})

        report = await _restore(tmp_path)

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("custom", RestoreFailureReason.NAME_CLASH)
        assert "custom" in failure.message

    async def test_builds_nothing_when_a_folder_cannot_be_listed(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        (tmp_path / "converters").write_text("a file where the folder should be")

        with caplog.at_level("WARNING", logger="pyrit.registry.registry_recipe_restore"):
            report = await _restore(tmp_path)

        (failure,) = report.failures
        assert (failure.kind, failure.name, failure.reason) == (
            RegistryRecipeKind.CONVERTERS,
            None,
            RestoreFailureReason.UNREADABLE_STORAGE,
        )
        assert str(tmp_path) not in failure.message
        assert str(tmp_path / "converters") in caplog.text
        assert report.restored == []
        assert "contoso_gpt4o" not in TargetRegistry.get_registry_singleton().instances

    async def test_raises_a_listing_bug_instead_of_blaming_storage(self, tmp_path: Path) -> None:
        with patch.object(RegistryRecipeStorage, "list_recipes", side_effect=KeyError("bug")):
            with pytest.raises(KeyError, match="bug"):
                await _restore(tmp_path)

    async def test_a_build_error_fails_only_that_entry(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "bad", _IDENTITY_TARGET)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "good", _IDENTITY_TARGET)

        report = await _restore(tmp_path, build_async=_raising_builder({"bad": ValueError("could not be coerced")}))

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("bad", RestoreFailureReason.BUILD_ERROR)
        assert failure.version
        assert [ref.name for ref in report.restored] == ["good"]
        assert "bad" not in TargetRegistry.get_registry_singleton().instances

    async def test_reports_the_reason_a_builder_refuses_an_entry_with(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "refused", _IDENTITY_TARGET)
        refusal = RecipeRejectedError(reason=RestoreFailureReason.INVALID_RECIPE, message="Not a valid request.")

        report = await _restore(tmp_path, build_async=_raising_builder({"refused": refusal}))

        (failure,) = report.failures
        assert (failure.reason, failure.message) == (RestoreFailureReason.INVALID_RECIPE, "Not a valid request.")

    async def test_reports_a_reserved_name_as_invalid_and_a_taken_one_as_a_clash(self, tmp_path: Path) -> None:
        ConverterRegistry.get_registry_singleton().instances.register(Base64Converter(), name="taken")
        base64 = {"class_module": "pyrit.converter.base64_converter", "class_name": "Base64Converter", "params": {}}
        _save(tmp_path, RegistryRecipeKind.CONVERTERS, "preview", base64)
        _save(tmp_path, RegistryRecipeKind.CONVERTERS, "taken", base64)

        report = await _restore(tmp_path)

        failures = _failures_by_name(report.failures)
        assert failures["preview"].reason is RestoreFailureReason.INVALID_RECIPE
        assert failures["taken"].reason is RestoreFailureReason.NAME_CLASH

    async def test_reports_a_technique_name_a_scenario_cant_use_as_invalid(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "value", {"type": "PromptSendingAttack"})

        report = await _restore(tmp_path)

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("value", RestoreFailureReason.INVALID_RECIPE)
        assert "ScenarioTechnique" in failure.message
