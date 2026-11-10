# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyrit.backend.services.saved_entries import (
    NotSaveableError,
    SavedEntryConflictError,
    prepare_saved_copy_params_async,
    prepare_saved_params_async,
    refuse_temporary_references,
    save_then_register_async,
)
from pyrit.converter import Base64Converter, ToneConverter
from pyrit.models import ComponentType, Parameter, RegistryReference
from pyrit.models.catalog import ComponentRecipe, RegistryEntryOrigin, RegistryRecipeKind
from pyrit.prompt_target import RoundRobinTarget
from pyrit.registry import ConverterRegistry, ScorerRegistry, TargetRegistry
from pyrit.registry.instance_registry import RegistryNameTakenError, registration_origin
from pyrit.score import SubStringScorer
from unit.mocks import MockPromptTarget

_REGISTRIES = (TargetRegistry, ConverterRegistry, ScorerRegistry)


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    for registry in _REGISTRIES:
        registry.reset_registry_singleton()
    yield
    for registry in _REGISTRIES:
        registry.reset_registry_singleton()


def _reference(name: str, component_type: ComponentType) -> Parameter:
    return Parameter(name=name, description=name, reference=RegistryReference(component_type=component_type))


@pytest.fixture
def startup_and_temporary_entries() -> None:
    targets = TargetRegistry.get_registry_singleton().instances
    converters = ConverterRegistry.get_registry_singleton().instances
    scorers = ScorerRegistry.get_registry_singleton().instances
    with registration_origin(RegistryEntryOrigin.CONFIGURATION):
        targets.register(MockPromptTarget(), name="startup_chat")
        converters.register(Base64Converter(), name="startup_base64")
        scorers.register(SubStringScorer(substring="x", categories=["leak"]), name="startup_leak")
    targets.register(MockPromptTarget(), name="launch_chat")
    converters.register(Base64Converter(), name="launch_base64")
    scorers.register(SubStringScorer(substring="y", categories=["leak"]), name="launch_leak")


@pytest.mark.usefixtures("patch_central_database", "startup_and_temporary_entries")
class TestRefuseTemporaryReferences:
    @pytest.mark.parametrize(
        ("component_type", "value", "named"),
        [
            (ComponentType.TARGET, "launch_chat", "target 'launch_chat'"),
            (ComponentType.CONVERTER, "launch_base64", "converter 'launch_base64'"),
            (ComponentType.SCORER, ["startup_leak", "launch_leak"], "scorer 'launch_leak'"),
        ],
    )
    def test_refuses_an_entry_that_is_gone_after_a_restart(
        self, component_type: ComponentType, value: object, named: str
    ) -> None:
        with pytest.raises(NotSaveableError) as refusal:
            refuse_temporary_references(parameters=[_reference("uses", component_type)], params={"uses": value})

        assert str(refusal.value) == (
            f"Uses temporary {named}, which won't exist after a restart. It can still be created without saving it."
        )

    @pytest.mark.parametrize(
        ("component_type", "value"),
        [
            (ComponentType.TARGET, "startup_chat"),
            (ComponentType.CONVERTER, ["startup_base64"]),
            (ComponentType.SCORER, "startup_leak"),
            (ComponentType.TARGET, "not_registered"),
            (ComponentType.TARGET, None),
            (ComponentType.TARGET, ""),
        ],
    )
    def test_accepts_entries_that_come_back_or_a_reference_left_unset(
        self, component_type: ComponentType, value: object
    ) -> None:
        refuse_temporary_references(parameters=[_reference("uses", component_type)], params={"uses": value})

    def test_refuses_a_kind_that_saved_entries_cant_use(self) -> None:
        with pytest.raises(NotSaveableError) as refusal:
            refuse_temporary_references(
                parameters=[_reference("uses", ComponentType.SCENARIO)], params={"uses": "nightly"}
            )

        assert str(refusal.value) == (
            "Uses scenario 'nightly', which saved entries can't use yet. It can still be created without saving it."
        )


async def test_a_copy_to_save_cant_hold_a_credential_even_one_its_class_lets_copies_change() -> None:
    registry = MagicMock()
    registry.get_registered_class_metadata.return_value = SimpleNamespace(
        parameters=[
            Parameter(name="token", description="token", sensitive=True),
            Parameter(name="tone", description="tone"),
        ]
    )

    with pytest.raises(NotSaveableError) as refusal:
        await prepare_saved_copy_params_async(
            registry=registry,
            type_name="ToneConverter",
            cls=ToneConverter,
            params={"token": "typed-key", "tone": "formal"},
        )

    assert str(refusal.value) == (
        "Saved entries can't hold credentials ('token'). It can still be created without saving it."
    )
    assert await prepare_saved_copy_params_async(
        registry=registry, type_name="ToneConverter", cls=ToneConverter, params={"tone": "formal"}
    ) == {"tone": "formal"}


class _LaunchScriptBase64Converter(Base64Converter):
    """A converter class that only a launch script registers."""


@pytest.mark.usefixtures("patch_central_database", "startup_and_temporary_entries")
class TestPrepareSavedParams:
    async def test_refuses_a_class_that_a_launch_script_added(self) -> None:
        registry = ConverterRegistry.get_registry_singleton()
        with registration_origin(RegistryEntryOrigin.TEMPORARY):
            registry.register_class(_LaunchScriptBase64Converter)

        with pytest.raises(NotSaveableError) as refusal:
            await prepare_saved_params_async(
                registry=registry, type_name="_LaunchScriptBase64Converter", cls=_LaunchScriptBase64Converter, params={}
            )

        assert str(refusal.value) == (
            "'_LaunchScriptBase64Converter' was added by a scan's launch script, so a saved entry of it couldn't be "
            "restored after a restart. It can still be created without saving it."
        )

    async def test_checks_a_class_without_cached_metadata_the_way_restore_does(self) -> None:
        with (
            patch.object(TargetRegistry, "get_registered_class_metadata", return_value=None),
            pytest.raises(NotSaveableError, match="Uses temporary target 'launch_chat'"),
        ):
            await prepare_saved_params_async(
                registry=TargetRegistry.get_registry_singleton(),
                type_name="RoundRobinTarget",
                cls=RoundRobinTarget,
                params={"targets": ["launch_chat"]},
            )


class TestSaveThenRegister:
    @pytest.mark.parametrize(
        ("error", "raised"),
        [
            (RegistryNameTakenError("Instance 'formal' already exists"), SavedEntryConflictError),
            (ValueError("Instance name 'formal' is reserved"), ValueError),
        ],
    )
    async def test_reports_only_a_taken_name_as_a_conflict_and_deletes_the_recipe(
        self, tmp_path: Path, error: Exception, raised: type[Exception]
    ) -> None:
        def register(_metadata: dict[str, Any]) -> None:
            raise error

        with pytest.raises(raised) as refusal:
            await save_then_register_async(
                kind=RegistryRecipeKind.CONVERTERS,
                name="formal",
                recipe=ComponentRecipe(class_module="pyrit.converter", class_name="Base64Converter"),
                recipes_source=str(tmp_path),
                register=register,
            )

        assert type(refusal.value) is raised
        assert not (tmp_path / "converters" / "formal.json").exists()
