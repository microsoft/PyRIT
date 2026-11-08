# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import json
import os
import sys
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from pyrit.backend.models.techniques import CreateTechniqueRequest
from pyrit.converter import Base64Converter, ColloquialWordswapConverter
from pyrit.models import Parameter
from pyrit.models.catalog import (
    RegistryRecipeKind,
    RestoreFailure,
    RestoreFailureReason,
    SavedEntryRef,
    TechniqueRecipe,
)
from pyrit.prompt_target import OpenAIChatTarget, RoundRobinTarget
from pyrit.registry import AttackRegistry, AttackTechniqueRegistry, ConverterRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.instance_registry import SAVED_ENTRY_METADATA_KEY
from pyrit.registry.registry_recipe_restore import RegistryRecipeRestorer
from pyrit.registry.registry_recipe_storage import RegistryRecipeStorage

_OPENAI_MODULE = "pyrit.prompt_target.openai.openai_chat_target"
_IDENTITY_TARGET = {
    "class_module": _OPENAI_MODULE,
    "class_name": "OpenAIChatTarget",
    "params": {
        "auth_mode": "identity",
        "endpoint": "https://contoso.openai.azure.com/openai/v1",
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


@pytest.mark.usefixtures("patch_central_database")
class TestRestoreOrderAndRebuild:
    def test_restores_every_kind_after_the_entries_it_uses(self, tmp_path: Path) -> None:
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

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

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

    def test_marks_each_restored_entry_with_its_kind(self, tmp_path: Path) -> None:
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

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

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

    def test_finds_a_startup_entry_by_its_exact_name(self, tmp_path: Path) -> None:
        registry = TargetRegistry.get_registry_singleton()
        for name in ("ghost\ud800", "other\udcff"):
            startup_target = OpenAIChatTarget(
                endpoint="https://startup.openai.azure.com/openai/v1", model_name="gpt-4o", auth_mode="identity"
            )
            registry.instances.register(startup_target, name=name)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "a_pool", _round_robin("ghost\ud800", "other\udcff"))

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        assert report.failures == []
        assert isinstance(registry.instances.get("a_pool"), RoundRobinTarget)

    def test_reads_without_building_and_restores_what_was_read(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        restorer = RegistryRecipeRestorer(source=str(tmp_path))

        with patch.object(TargetRegistry, "create_instance_from_external_input") as build:
            saved = restorer.read()
        build.assert_not_called()
        with patch.object(RegistryRecipeStorage, "list_recipes", side_effect=AssertionError("read again")):
            report = restorer.restore(saved=saved)

        assert report.failures == []
        assert [ref.name for ref in report.restored] == ["contoso_gpt4o"]

    def test_orders_entries_of_the_same_kind(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "a_pool", _round_robin("b_member", "c_member"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "b_member", _IDENTITY_TARGET)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "c_member", _IDENTITY_TARGET)

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

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
    def test_a_technique_waits_for_its_saved_adversarial_target(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", {**_IDENTITY_TARGET, "class_name": "NoSuchTarget"})
        without_converters = {key: value for key, value in _TECHNIQUE.items() if key != "request_converters"}
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "formal_red_team", without_converters)
        startup_target = OpenAIChatTarget(
            endpoint="https://startup.openai.azure.com/openai/v1", model_name="gpt-4o", auth_mode="identity"
        )
        TargetRegistry.get_registry_singleton().instances.register(startup_target, name="contoso_gpt4o")

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        failure = _failures_by_name(report.failures)["formal_red_team"]
        assert failure.reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert failure.dependency == SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="contoso_gpt4o")

    @pytest.mark.parametrize("field", ["request_converters", "response_converters"])
    def test_a_technique_waits_for_a_saved_converter_in_either_list(self, tmp_path: Path, field: str) -> None:
        ConverterRegistry.get_registry_singleton().instances.register(Base64Converter(), name="shared_name")
        _save_text(tmp_path, RegistryRecipeKind.CONVERTERS, "shared_name", "{not json")
        _save(
            tmp_path, RegistryRecipeKind.TECHNIQUES, "uses_shared", {"type": "RedTeamingAttack", field: ["shared_name"]}
        )

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        failure = _failures_by_name(report.failures)["uses_shared"]
        assert failure.reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert failure.dependency == SavedEntryRef(kind=RegistryRecipeKind.CONVERTERS, name="shared_name")

    def test_reports_an_unknown_attack_type(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "unknown_attack", {"type": "NoSuchAttack"})

        (failure,) = RegistryRecipeRestorer(source=str(tmp_path)).restore().failures

        assert (failure.name, failure.reason) == ("unknown_attack", RestoreFailureReason.UNKNOWN_CLASS)

    def test_technique_parameters_are_checked_as_strict_json(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        without_converters = {key: value for key, value in _TECHNIQUE.items() if key != "request_converters"}
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "loose", {**without_converters, "params": {"max_turns": "5"}})

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("loose", RestoreFailureReason.BUILD_ERROR)

    def test_reports_dependency_loops_and_entries_waiting_on_them(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "loop_a", _round_robin("loop_b"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "loop_b", _round_robin("loop_a"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "waiting", _round_robin("loop_a"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "self_loop", _round_robin("self_loop"))

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        failures = _failures_by_name(report.failures)
        assert report.restored == []
        assert failures["loop_a"].reason is RestoreFailureReason.DEPENDENCY_LOOP
        assert failures["loop_b"].reason is RestoreFailureReason.DEPENDENCY_LOOP
        assert failures["self_loop"].reason is RestoreFailureReason.DEPENDENCY_LOOP
        assert failures["waiting"].reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert failures["waiting"].dependency == SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="loop_a")

    def test_names_a_loop_member_as_the_reason_for_a_loop(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "a", _round_robin("c", "b"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "b", _round_robin("a"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "c", _round_robin("d"))
        _save(tmp_path, RegistryRecipeKind.TARGETS, "d", _round_robin("c"))

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        failures = _failures_by_name(report.failures)
        assert failures["a"].reason is RestoreFailureReason.DEPENDENCY_LOOP
        assert failures["a"].dependency == SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="b")

    def test_reports_missing_and_failed_dependencies(self, tmp_path: Path) -> None:
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

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        failures = _failures_by_name(report.failures)
        assert failures["broken"].reason is RestoreFailureReason.UNKNOWN_CLASS
        assert failures["uses_broken"].reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert failures["uses_ghost"].reason is RestoreFailureReason.MISSING_DEPENDENCY
        assert failures["uses_ghost"].dependency == SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="ghost")
        assert failures["bad"].reason is RestoreFailureReason.INVALID_RECIPE
        assert failures["uses_unparsable"].reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert failures["uses_unparsable"].dependency == SavedEntryRef(kind=RegistryRecipeKind.CONVERTERS, name="bad")

    def test_never_imports_a_module_named_by_a_file(self, tmp_path: Path) -> None:
        module = "tests_registry_restore_never_imported"
        _save(tmp_path, RegistryRecipeKind.TARGETS, "moved", {**_IDENTITY_TARGET, "class_module": module})
        _save(tmp_path, RegistryRecipeKind.TARGETS, "unknown", {"class_module": "os", "class_name": "system"})

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        failures = _failures_by_name(report.failures)
        assert failures["moved"].reason is RestoreFailureReason.MODULE_MISMATCH
        assert _OPENAI_MODULE in failures["moved"].message
        assert failures["unknown"].reason is RestoreFailureReason.UNKNOWN_CLASS
        assert module not in sys.modules

    def test_refuses_a_stored_credential(self, tmp_path: Path) -> None:
        stored_key = {**_IDENTITY_TARGET, "params": {**_IDENTITY_TARGET["params"], "api_key": "sk-secret-value"}}
        _save(tmp_path, RegistryRecipeKind.TARGETS, "stored_key", stored_key)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "good", _IDENTITY_TARGET)

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("stored_key", RestoreFailureReason.UNSUPPORTED_CREDENTIAL)
        assert "sk-secret-value" not in failure.message
        assert [ref.name for ref in report.restored] == ["good"]

    def test_restores_a_class_whose_optional_credentials_are_left_empty(self, tmp_path: Path) -> None:
        _save(
            tmp_path,
            RegistryRecipeKind.TARGETS,
            "anonymous_agent",
            {
                "class_module": "pyrit.prompt_target.a2a_target",
                "class_name": "A2ATarget",
                "params": {"endpoint": "https://agent.example.test"},
            },
        )

        with patch.object(TargetRegistry, "create_named_instance") as build:
            report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        assert report.failures == []
        build.assert_called_once_with(
            name="anonymous_agent",
            type_name="A2ATarget",
            params={"endpoint": "https://agent.example.test"},
            registry_metadata={SAVED_ENTRY_METADATA_KEY: "targets"},
            external_input=True,
            allowed_path_params=frozenset(),
        )

    def test_refuses_a_class_that_needs_the_server_upload_folder(self, tmp_path: Path) -> None:
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

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert failure.reason is RestoreFailureReason.UNSUPPORTED_CLASS
        assert "uploader" not in TargetRegistry.get_registry_singleton().instances

    @pytest.mark.parametrize("auth_mode", [[], {}, ["identity"]])
    def test_reports_malformed_values_without_stopping_the_restore(self, tmp_path: Path, auth_mode: object) -> None:
        malformed = {**_IDENTITY_TARGET, "params": {**_IDENTITY_TARGET["params"], "auth_mode": auth_mode}}
        _save(tmp_path, RegistryRecipeKind.TARGETS, "malformed", malformed)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "good", _IDENTITY_TARGET)

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        assert [failure.name for failure in report.failures] == ["malformed"]
        assert [ref.name for ref in report.restored] == ["good"]

    def test_a_planning_error_fails_only_that_entry(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "a_first", _IDENTITY_TARGET)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "b_second", _IDENTITY_TARGET)

        with patch.object(
            RegistryRecipeRestorer, "_parameter_references", side_effect=[RuntimeError("unexpected"), []]
        ):
            report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("a_first", RestoreFailureReason.INVALID_RECIPE)
        assert failure.message == "RuntimeError: unexpected"
        assert [ref.name for ref in report.restored] == ["b_second"]

    @pytest.mark.parametrize(
        ("endpoint", "shown"),
        [
            ("https://evil.example/openai/v1?sig=SYNTHETIC-SAS#frag", "'https://evil.example/openai/v1"),
            ("https://evil.example/openai/v1'part?sig=SYNTHETIC-SAS", "'https://evil.example/openai/v1'part"),
            ("https://evil.example/openai/v1?sig=SYNTH\tETIC-SAS", "'https://evil.example/openai/v1"),
            ("evil.example/openai/v1?api-version=1 &sig=SYNTHETIC-SAS", "'evil.example/openai/v1"),
            ("https://evil.example/openai/v1#SYNTHETIC-SAS", "'https://evil.example/openai/v1"),
            ("https://user:hunter2@evil.example/openai/v1?sig=SYNTHETIC-SAS", None),
            ("https://user:hun\nter2@evil.example/openai/v1", None),
            ("//user:hunter2@evil.example/openai/v1", None),
        ],
    )
    def test_build_errors_drop_credentials_from_urls(self, tmp_path: Path, endpoint: str, shown: str | None) -> None:
        no_endpoint = {**_IDENTITY_TARGET, "params": {"auth_mode": "identity", "model_name": "gpt-4o"}}
        _save(tmp_path, RegistryRecipeKind.TARGETS, "server_endpoint", no_endpoint)

        with patch.dict(os.environ, {"OPENAI_CHAT_ENDPOINT": endpoint}):
            report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert failure.reason is RestoreFailureReason.BUILD_ERROR
        if shown is None:
            assert failure.message == "ValueError (message hidden because it may include a credential)"
        else:
            assert failure.message.startswith("ValueError: Identity-based authentication requires")
            assert failure.message.endswith(f"{shown} (rest hidden because it may include a credential)")
        for secret in ("hunter2", "hun", "ter2", "SYNTHETIC", "SYNTH", "ETIC", "frag"):
            assert secret not in failure.message

    def test_build_errors_keep_text_without_a_credential(self, tmp_path: Path) -> None:
        no_endpoint = {**_IDENTITY_TARGET, "params": {"auth_mode": "identity", "model_name": "gpt-4o"}}
        _save(tmp_path, RegistryRecipeKind.TARGETS, "server_endpoint", no_endpoint)

        with patch.dict(os.environ, {"OPENAI_CHAT_ENDPOINT": "https://api.example.test/v1"}):
            report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert "'https://api.example.test/v1'. Pass auth_mode" in failure.message

    def test_error_text_that_is_not_a_valid_url_fails_only_that_entry(self, tmp_path: Path) -> None:
        malformed = {
            **_IDENTITY_TARGET,
            "params": {**_IDENTITY_TARGET["params"], "temperature": "https://[x?sig=SECRET"},
        }
        _save(tmp_path, RegistryRecipeKind.TARGETS, "malformed", malformed)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "good", _IDENTITY_TARGET)

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("malformed", RestoreFailureReason.BUILD_ERROR)
        assert failure.version
        assert "could not be coerced" in failure.message
        assert "SECRET" not in failure.message
        assert [ref.name for ref in report.restored] == ["good"]

    def test_error_text_with_a_percent_encoded_credential_is_hidden(self, tmp_path: Path) -> None:
        encoded = {
            **_IDENTITY_TARGET,
            "params": {**_IDENTITY_TARGET["params"], "temperature": "https://host/v1%3Fsig%3DSECRET"},
        }
        _save(tmp_path, RegistryRecipeKind.TARGETS, "encoded", encoded)

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("encoded", RestoreFailureReason.BUILD_ERROR)
        assert failure.message.endswith("(message hidden because it may include a credential)")
        assert "SECRET" not in failure.message

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "got https://host/v1?sig=SECRET",
                "ValueError: got https://host/v1 (rest hidden because it may include a credential)",
            ),
            (
                "got https:host/p#SECRET",
                "ValueError: got https:host/p (rest hidden because it may include a credential)",
            ),
            (
                "got https://host/v1#a?b=SECRET",
                "ValueError: got https://host/v1 (rest hidden because it may include a credential)",
            ),
            (
                "got https://host/v1? sig=SEC\tRET tail",
                "ValueError: got https://host/v1 (rest hidden because it may include a credential)",
            ),
            (
                "got host/v1?api-version=1 &sig=SECRET",
                "ValueError: got host/v1 (rest hidden because it may include a credential)",
            ),
            (
                "got 'https://host/v1#SECRET' (again 'https://host/v1#SECRET')",
                "ValueError: got 'https://host/v1 (rest hidden because it may include a credential)",
            ),
            ("?sig=SECRET", "ValueError (message hidden because it may include a credential)"),
            (" \t?sig=SECRET", "ValueError (message hidden because it may include a credential)"),
            ("could not be coerced to float: 'hot'", "ValueError: could not be coerced to float: 'hot'"),
            (
                "Missing required parameters: ['objective_target']",
                "ValueError: Missing required parameters: ['objective_target']",
            ),
            ("got https://host/v1", "ValueError: got https://host/v1"),
            ("got //user:SECRET@host/v1", "ValueError (message hidden because it may include a credential)"),
            ("got https://user:SEC\nRET@host/v1", "ValueError (message hidden because it may include a credential)"),
            ("got https://user:SE?CRET@host/v1", "ValueError (message hidden because it may include a credential)"),
            ("got https://user:SECRET\uff20host/v1", "ValueError (message hidden because it may include a credential)"),
            (
                "got https://host/v1\uff1fsig=SECRET",
                "ValueError: got https://host/v1 (rest hidden because it may include a credential)",
            ),
            ("contact admin@example.com", "ValueError (message hidden because it may include a credential)"),
            ("got https://host/v1%3Fsig%3DSECRET", "ValueError (message hidden because it may include a credential)"),
            ("got https://user:SECRET%40host/v1", "ValueError (message hidden because it may include a credential)"),
            (
                "could not convert '\uff10\uff0e\uff15' or 10\u00b3",
                "ValueError: could not convert '\uff10\uff0e\uff15' or 10\u00b3",
            ),
            (
                "got '\uff15' at https://host/v1\ufe56sig=SECRET",
                "ValueError: got '\uff15' at https://host/v1 (rest hidden because it may include a credential)",
            ),
        ],
    )
    def test_safe_messages_drop_queries_fragments_and_user_info(self, text: str, expected: str) -> None:
        assert RegistryRecipeRestorer._safe_message(ValueError(text)) == expected

    @pytest.mark.parametrize(
        "mark", ["?", "#", "\u2047", "\u2048", "\u2049", "\ufe16", "\ufe56", "\ufe5f", "\uff03", "\uff1f"]
    )
    def test_safe_message_cuts_at_any_form_of_a_query_or_fragment_mark(self, mark: str) -> None:
        message = RegistryRecipeRestorer._safe_message(ValueError(f"got https://host/v1{mark}SECRET"))

        assert message == "ValueError: got https://host/v1 (rest hidden because it may include a credential)"

    @pytest.mark.parametrize("mark", ["@", "\ufe6b", "\uff20"])
    def test_safe_message_hides_text_with_any_form_of_an_at_sign(self, mark: str) -> None:
        message = RegistryRecipeRestorer._safe_message(ValueError(f"got https://USER:SECRET{mark}host/v1"))

        assert message == "ValueError (message hidden because it may include a credential)"

    @pytest.mark.parametrize("mark", ["%", "\ufe6a", "\uff05"])
    def test_safe_message_hides_text_with_any_form_of_a_percent_sign(self, mark: str) -> None:
        message = RegistryRecipeRestorer._safe_message(ValueError(f"got https://USER:SECRET{mark}40host/v1"))

        assert message == "ValueError (message hidden because it may include a credential)"

    def test_safe_message_folds_one_character_at_a_time(self) -> None:
        marks = "\u0315" * 50 + "\u0300" * 50
        text = f"a{marks}\uff1fsig=SECRET"
        with patch(
            "pyrit.registry.registry_recipe_restore.unicodedata.normalize", wraps=unicodedata.normalize
        ) as normalize:
            message = RegistryRecipeRestorer._safe_message(ValueError(text))

        assert message == f"ValueError: a{marks} (rest hidden because it may include a credential)"
        assert {len(call.args[1]) for call in normalize.call_args_list} == {1}
        assert normalize.call_count <= 2 * len(text)

    def test_safe_message_survives_an_error_that_cannot_be_printed(self) -> None:
        class UnprintableError(Exception):
            def __str__(self) -> str:
                raise RuntimeError("cannot print")

        assert RegistryRecipeRestorer._safe_message(UnprintableError()) == "UnprintableError"

    def test_safe_message_ignores_string_methods_an_error_overrides(self) -> None:
        class KeepEverything(str):
            __slots__ = ()

            def __getitem__(self, key: Any) -> str:
                return str.__str__(self)

        class Exploding(str):
            __slots__ = ()

            def __contains__(self, item: object) -> bool:
                raise RuntimeError("cannot search")

        class QuotingError(Exception):
            def __init__(self, text: str) -> None:
                super().__init__()
                self.text = text

            def __str__(self) -> str:
                return self.text

        keep = RegistryRecipeRestorer._safe_message(QuotingError(KeepEverything("got https://host/v1?sig=SECRET")))
        exploding = RegistryRecipeRestorer._safe_message(QuotingError(Exploding("got https://host/v1")))

        assert keep == "QuotingError: got https://host/v1 (rest hidden because it may include a credential)"
        assert exploding == "QuotingError: got https://host/v1"

    def test_reports_a_reference_name_that_is_not_valid_text(self, tmp_path: Path) -> None:
        _save_text(
            tmp_path,
            RegistryRecipeKind.TARGETS,
            "pool",
            '{"class_module": "pyrit.prompt_target.round_robin_target", "class_name": "RoundRobinTarget", '
            '"params": {"targets": ["ghost\\ud800"]}}',
        )

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert failure.reason is RestoreFailureReason.MISSING_DEPENDENCY
        assert failure.dependency == SavedEntryRef(kind=RegistryRecipeKind.TARGETS, name="ghost\ud800")
        sent = json.loads(json.dumps(report.model_dump(mode="json"), ensure_ascii=False).encode("utf-8"))
        assert sent["failures"][0]["dependency"]["name"] == "ghost\\ud800"

    def test_refuses_a_stored_credential_in_attack_parameters(self, tmp_path: Path) -> None:
        attack_registry = AttackRegistry.get_registry_singleton()
        original_get_class_metadata = attack_registry.get_class_metadata

        def with_credential(cls: type) -> Any:
            metadata = original_get_class_metadata(cls)
            credential = Parameter(name="api_key", description="", sensitive=True)
            return dataclasses.replace(metadata, parameters=(*metadata.parameters, credential))

        _save(
            tmp_path,
            RegistryRecipeKind.TECHNIQUES,
            "keyed",
            {"type": "RedTeamingAttack", "params": {"api_key": "sk-x"}},
        )

        with patch.object(attack_registry, "get_class_metadata", side_effect=with_credential):
            report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("keyed", RestoreFailureReason.UNSUPPORTED_CREDENTIAL)
        assert "sk-x" not in failure.message

    def test_an_empty_reference_is_left_unset(self, tmp_path: Path) -> None:
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

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        assert report.failures == []
        assert [ref.name for ref in report.restored] == ["puzzled"]

    def test_follows_converter_and_scorer_references(self, tmp_path: Path) -> None:
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

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        failures = _failures_by_name(report.failures)
        assert failures["selective"].dependency == SavedEntryRef(
            kind=RegistryRecipeKind.CONVERTERS, name="ghost_converter"
        )
        assert failures["composite"].dependency == SavedEntryRef(kind=RegistryRecipeKind.SCORERS, name="ghost_scorer")
        assert {failure.reason for failure in report.failures} == {RestoreFailureReason.MISSING_DEPENDENCY}

    def test_refuses_reserved_technique_names_and_tags(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "types", {"type": "RedTeamingAttack"})
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "tagged_all", {"type": "RedTeamingAttack", "tags": ["all"]})

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        failures = _failures_by_name(report.failures)
        assert failures["types"].reason is RestoreFailureReason.INVALID_RECIPE
        assert failures["tagged_all"].reason is RestoreFailureReason.INVALID_RECIPE
        assert report.restored == []

    def test_rejects_server_paths_the_way_the_api_does(self, tmp_path: Path) -> None:
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

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert failure.reason is RestoreFailureReason.BUILD_ERROR
        assert "wordswap_path" in failure.message
        assert "wordswap" not in ConverterRegistry.get_registry_singleton().instances

    def test_lets_a_target_set_the_path_parameters_its_class_allows_callers(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        recipes = tmp_path / "recipes"
        _save(
            recipes,
            RegistryRecipeKind.TARGETS,
            "copilot",
            {
                "class_module": "pyrit.prompt_target.github_copilot_target",
                "class_name": "GitHubCopilotTarget",
                "params": {"model_name": "gpt-5-mini", "working_directory": str(workspace)},
            },
        )

        with patch.dict(sys.modules, {"copilot": ModuleType("copilot")}):
            report = RegistryRecipeRestorer(source=str(recipes)).restore()

        assert report.failures == []
        target = TargetRegistry.get_registry_singleton().instances.get("copilot")
        assert target is not None
        assert target._working_directory == str(workspace.resolve())

    def test_gives_only_targets_the_path_parameters_their_class_allows_callers(self, tmp_path: Path) -> None:
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

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert failure.reason is RestoreFailureReason.BUILD_ERROR
        assert "wordswap_path" in failure.message
        assert "wordswap" not in ConverterRegistry.get_registry_singleton().instances

    def test_reports_a_name_taken_at_startup_and_fails_its_dependents(self, tmp_path: Path) -> None:
        startup_target = OpenAIChatTarget(
            endpoint="https://startup.openai.azure.com/openai/v1", model_name="gpt-4o", auth_mode="identity"
        )
        TargetRegistry.get_registry_singleton().instances.register(startup_target, name="contoso_gpt4o")
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "pool", _round_robin("contoso_gpt4o"))

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        failures = _failures_by_name(report.failures)
        assert failures["contoso_gpt4o"].reason is RestoreFailureReason.NAME_CLASH
        assert failures["pool"].reason is RestoreFailureReason.FAILED_DEPENDENCY
        assert TargetRegistry.get_registry_singleton().instances.get("contoso_gpt4o") is startup_target

    def test_reports_a_technique_name_that_matches_a_tag_in_another_case(self, tmp_path: Path) -> None:
        without_converters = {key: value for key, value in _TECHNIQUE.items() if key != "request_converters"}
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "formal_red_team", {**without_converters, "tags": ["Custom"]})
        assert RegistryRecipeRestorer(source=str(tmp_path)).restore().failures == []
        TargetRegistry.reset_registry_singleton()
        (tmp_path / "techniques" / "formal_red_team.json").unlink()
        _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "custom", {**without_converters, "tags": []})

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("custom", RestoreFailureReason.NAME_CLASH)
        assert "custom" in failure.message

    def test_builds_nothing_when_a_folder_cannot_be_listed(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        original_list_recipes = RegistryRecipeStorage.list_recipes

        def list_recipes(storage: RegistryRecipeStorage) -> Any:
            if storage._kind is RegistryRecipeKind.CONVERTERS:
                raise PermissionError(13, "Permission denied", str(tmp_path / "converters"))
            return original_list_recipes(storage)

        with patch.object(RegistryRecipeStorage, "list_recipes", autospec=True, side_effect=list_recipes):
            report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert (failure.kind, failure.name, failure.reason) == (
            RegistryRecipeKind.CONVERTERS,
            None,
            RestoreFailureReason.UNREADABLE_STORAGE,
        )
        assert str(tmp_path) not in failure.message
        assert report.restored == []
        assert "contoso_gpt4o" not in TargetRegistry.get_registry_singleton().instances

    def test_builds_nothing_when_listing_a_folder_fails_unexpectedly(self, tmp_path: Path) -> None:
        _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
        original_list_recipes = RegistryRecipeStorage.list_recipes

        def list_recipes(storage: RegistryRecipeStorage) -> Any:
            if storage._kind is RegistryRecipeKind.SCORERS:
                raise ValueError("https://account.blob.core.windows.net/x?sig=SECRET")
            return original_list_recipes(storage)

        with patch.object(RegistryRecipeStorage, "list_recipes", autospec=True, side_effect=list_recipes):
            report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert (failure.kind, failure.reason) == (RegistryRecipeKind.SCORERS, RestoreFailureReason.UNREADABLE_STORAGE)
        assert "SECRET" not in failure.message
        assert report.restored == []

    def test_reports_constructor_errors_and_keeps_going(self, tmp_path: Path) -> None:
        bad_value = {**_IDENTITY_TARGET, "params": {**_IDENTITY_TARGET["params"], "temperature": "hot"}}
        _save(tmp_path, RegistryRecipeKind.TARGETS, "bad_value", bad_value)
        _save(tmp_path, RegistryRecipeKind.TARGETS, "good", _IDENTITY_TARGET)

        report = RegistryRecipeRestorer(source=str(tmp_path)).restore()

        (failure,) = report.failures
        assert (failure.name, failure.reason) == ("bad_value", RestoreFailureReason.BUILD_ERROR)
        assert failure.version
        assert [ref.name for ref in report.restored] == ["good"]
