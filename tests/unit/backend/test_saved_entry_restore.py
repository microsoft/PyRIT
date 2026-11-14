# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import os
import sys
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest

from pyrit.backend.services.saved_entry_restore import build_saved_entry_async
from pyrit.models.catalog import RegistryRecipeKind, RegistryRestoreReport, RestoreFailureReason
from pyrit.prompt_target import HTTPXAPITarget, OpenAIChatTarget
from pyrit.registry import AttackTechniqueRegistry, ConverterRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.registry_recipe_restore import RegistryRecipeRestorer

_IDENTITY_TARGET = {
    "class_module": "pyrit.prompt_target.openai.openai_chat_target",
    "class_name": "OpenAIChatTarget",
    "auth_mode": "identity",
    "params": {"endpoint": "https://contoso.openai.azure.com/openai/v1", "model_name": "gpt-4o"},
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
    folder = root / kind.value
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(document))


async def _restore(root: Path, *, upload_directory: str | None = None) -> RegistryRestoreReport:
    build_async = partial(build_saved_entry_async, upload_directory=upload_directory)
    return await RegistryRecipeRestorer(source=str(root)).restore_async(build_async=build_async)


@pytest.mark.usefixtures("patch_central_database")
async def test_restores_each_kind_through_its_create(tmp_path: Path) -> None:
    _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)
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
        RegistryRecipeKind.TECHNIQUES,
        "formal_red_team",
        {
            "type": "RedTeamingAttack",
            "params": {"max_turns": 5},
            "tags": ["custom"],
            "adversarial_chat": "contoso_gpt4o",
            "adversarial_system_prompt": "You are a red teaming assistant.",
            "request_converters": ["tone_formal"],
        },
    )

    report = await _restore(tmp_path)

    assert report.failures == []
    target = TargetRegistry.get_registry_singleton().instances.get("contoso_gpt4o")
    assert isinstance(target, OpenAIChatTarget)
    assert target._auth_mode == "identity"
    converter = ConverterRegistry.get_registry_singleton().instances.get("tone_formal")
    assert converter is not None
    assert converter._converter_target is target
    assert ScorerRegistry.get_registry_singleton().instances.get("refusal") is not None
    factory = AttackTechniqueRegistry.get_registry_singleton().instances.get("formal_red_team")
    assert factory is not None
    assert factory.technique_tags == ["custom"]


@pytest.mark.usefixtures("patch_central_database")
async def test_a_target_its_create_refuses_fails_only_that_entry(tmp_path: Path) -> None:
    _save(
        tmp_path,
        RegistryRecipeKind.TARGETS,
        "copilot",
        {
            "class_module": "pyrit.prompt_target.github_copilot_target",
            "class_name": "GitHubCopilotTarget",
            "params": {"model_name": "gpt-5-mini"},
        },
    )
    _save(tmp_path, RegistryRecipeKind.TARGETS, "contoso_gpt4o", _IDENTITY_TARGET)

    with patch.dict(sys.modules, {"copilot": ModuleType("copilot")}), patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
        report = await _restore(tmp_path)

    (failure,) = report.failures
    assert (failure.name, failure.reason) == ("copilot", RestoreFailureReason.BUILD_ERROR)
    assert "GitHub Copilot requires a GitHub token" in failure.message
    assert [ref.name for ref in report.restored] == ["contoso_gpt4o"]


@pytest.mark.usefixtures("patch_central_database")
async def test_a_target_that_uploads_files_gets_this_servers_upload_folder(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    recipes = tmp_path / "recipes"
    _save(
        recipes,
        RegistryRecipeKind.TARGETS,
        "uploader",
        {
            "class_module": "pyrit.prompt_target.http_target.httpx_api_target",
            "class_name": "HTTPXAPITarget",
            "params": {"http_url": "https://example.test/upload"},
        },
    )

    report = await _restore(recipes, upload_directory=str(uploads))

    assert report.failures == []
    target = TargetRegistry.get_registry_singleton().instances.get("uploader")
    assert isinstance(target, HTTPXAPITarget)
    assert target.allowed_upload_directory == uploads.resolve()


@pytest.mark.usefixtures("patch_central_database")
async def test_a_target_that_uploads_files_fails_where_the_server_sets_no_upload_folder(tmp_path: Path) -> None:
    _save(
        tmp_path,
        RegistryRecipeKind.TARGETS,
        "uploader",
        {
            "class_module": "pyrit.prompt_target.http_target.httpx_api_target",
            "class_name": "HTTPXAPITarget",
            "params": {"http_url": "https://example.test/upload"},
        },
    )

    (failure,) = (await _restore(tmp_path)).failures

    assert (failure.name, failure.reason) == ("uploader", RestoreFailureReason.BUILD_ERROR)
    assert "target_upload_directory" in failure.message


async def test_a_technique_its_create_request_refuses_is_invalid(tmp_path: Path) -> None:
    _save(tmp_path, RegistryRecipeKind.TECHNIQUES, "types", {"type": "RedTeamingAttack"})

    (failure,) = (await _restore(tmp_path)).failures

    assert (failure.name, failure.reason) == ("types", RestoreFailureReason.INVALID_RECIPE)
    assert failure.message.startswith("Not a valid create request: name:")
    assert "types" not in AttackTechniqueRegistry.get_registry_singleton().instances.get_names()
