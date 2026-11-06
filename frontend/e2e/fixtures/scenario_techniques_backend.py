# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Real preset, estimate, and execution routes over disposable, provider-free data."""

from __future__ import annotations

import asyncio
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from unittest.mock import patch

from pydantic import BaseModel

from frontend.e2e.fixtures import analytics_backend

if TYPE_CHECKING:
    from collections.abc import Generator

    from pyrit.memory import SQLiteMemory
    from pyrit.models.catalog.scenario import ScenarioRunSizeEstimate, ScenarioRunSizeEstimateRequest
    from pyrit.scenario.core.scenario_context import ScenarioContext


class EstimateControl(BaseModel):
    """Explicit one-shot faults for the otherwise real estimate service."""

    action: Literal["reset", "status", "hold", "release", "fail"]


def _register_components() -> None:
    from pyrit.converter import Base64Converter, ROT13Converter
    from pyrit.executor.attack import AttackConverterConfig, PromptSendingAttack
    from pyrit.models import Message, MessagePiece, SeedObjective
    from pyrit.prompt_normalizer import ConverterConfiguration
    from pyrit.prompt_target import PromptTarget, TargetCapabilities, TargetConfiguration
    from pyrit.registry import AttackTechniqueRegistry, ScenarioRegistry, TargetRegistry
    from pyrit.scenario.core import (
        AtomicAttack,
        AttackTechniqueFactory,
        BaselineAttackPolicy,
        DatasetAttackConfiguration,
        Scenario,
    )
    from pyrit.scenario.core.matrix_atomic_attack_builder import build_matrix_atomic_attacks
    from pyrit.score import SubStringScorer

    class TechniqueAcceptanceTarget(PromptTarget):
        """Respond locally so real attacks and scores can be persisted."""

        _DEFAULT_CONFIGURATION = TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_multi_message_pieces=True,
                supports_system_prompt=True,
                supports_editable_history=True,
            )
        )

        async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
            return [
                MessagePiece(
                    role="assistant",
                    original_value="Offline technique acceptance response.",
                    conversation_id=normalized_conversation[-1].message_pieces[0].conversation_id,
                ).to_message()
            ]

        def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
            if not normalized_conversation:
                raise ValueError("The offline acceptance target requires a message.")

    factories = [
        AttackTechniqueFactory(
            name="offline_plain",
            attack_class=PromptSendingAttack,
            description="Send the local objective unchanged.",
            technique_tags=["local", "single_turn"],
            supports_additional_request_converters=True,
        ),
        *[
            AttackTechniqueFactory(
                name=name,
                attack_class=PromptSendingAttack,
                description=f"Apply the local {converter.__class__.__name__} before sending.",
                technique_tags=["local", "encoded", "single_turn"],
                supports_additional_request_converters=True,
                attack_kwargs={
                    "attack_converter_config": AttackConverterConfig(
                        request_converters=ConverterConfiguration.from_converters(converters=[converter])
                    ),
                },
            )
            for name, converter in [("offline_base64", Base64Converter()), ("offline_rot13", ROT13Converter())]
        ],
    ]
    techniques = AttackTechniqueRegistry.get_registry_singleton()
    techniques.register_from_factories(factories)

    class TechniqueAcceptanceScenario(Scenario):
        """A local technique sweep with a scenario-relative default and optional baseline."""

        VERSION = 1
        MAX_CONCRETE_TECHNIQUES = 2
        TECHNIQUE_CLASS = techniques.build_technique_class_from_factories(
            class_name="TechniqueAcceptanceScenarioTechnique",
            factories=factories,
            default_names={"offline_plain"},
        )

        def __init__(self, *, scenario_result_id: str | None = None) -> None:
            super().__init__(
                version=self.VERSION,
                technique_class=self.TECHNIQUE_CLASS,
                default_dataset_config=DatasetAttackConfiguration(
                    seeds=[SeedObjective(value="Reply to this harmless local acceptance objective.")],
                    max_total=1,
                ),
                objective_scorer=SubStringScorer(substring="Offline technique acceptance"),
                scenario_result_id=scenario_result_id,
                uses_default_adversarial_target=False,
            )

        async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
            return await asyncio.to_thread(
                build_matrix_atomic_attacks, context=context, objective_scorer=self._objective_scorer
            )

    class TechniqueNoBaselineScenario(TechniqueAcceptanceScenario):
        """The same pool, with a different default and no baseline support."""

        BASELINE_ATTACK_POLICY = BaselineAttackPolicy.Forbidden
        TECHNIQUE_CLASS = techniques.build_technique_class_from_factories(
            class_name="TechniqueNoBaselineScenarioTechnique",
            factories=factories,
            default_names={"offline_rot13"},
        )

    class TechniqueOptionalBaselineScenario(TechniqueAcceptanceScenario):
        """The same pool, with a third default and baseline initially off."""

        BASELINE_ATTACK_POLICY = BaselineAttackPolicy.Disabled
        TECHNIQUE_CLASS = techniques.build_technique_class_from_factories(
            class_name="TechniqueOptionalBaselineScenarioTechnique",
            factories=factories,
            default_names={"offline_base64"},
        )

    targets = TargetRegistry.get_registry_singleton().instances
    for name in ["technique_offline", "technique_offline_alternate"]:
        targets.register(TechniqueAcceptanceTarget(), name=name)
    scenarios = ScenarioRegistry.get_registry_singleton()
    for name, scenario_class in [
        ("technique_acceptance", TechniqueAcceptanceScenario),
        ("technique_no_baseline", TechniqueNoBaselineScenario),
        ("technique_optional_baseline", TechniqueOptionalBaselineScenario),
    ]:
        scenarios.register_class(scenario_class, name=name)


def _guard_preset_paths(*, patches: ExitStack, directory: Path) -> None:
    from pyrit.registry.scenario_preset_storage import ScenarioPresetStorage

    root = directory.resolve()
    original_directory = ScenarioPresetStorage._local_directory
    original_lock = ScenarioPresetStorage._local_document_lock

    def local_directory(storage: ScenarioPresetStorage, *, create: bool = False) -> Path:
        resolved = original_directory(storage, create=False).resolve()
        if not resolved.is_relative_to(root):
            raise RuntimeError(f"Refusing preset storage outside the fixture: {resolved}")
        return original_directory(storage, create=create)

    @contextmanager
    def document_lock(storage: ScenarioPresetStorage, path: Path) -> Generator[None, None, None]:
        for location in (path, path.with_name(f".{path.name}{storage.LOCK_SUFFIX}")):
            if not location.resolve().is_relative_to(root):
                raise RuntimeError(f"Refusing preset mutation outside the fixture: {location}")
        with original_lock(storage, path):
            yield

    patches.enter_context(patch.object(ScenarioPresetStorage, "_local_directory", local_directory))
    patches.enter_context(patch.object(ScenarioPresetStorage, "_local_document_lock", document_lock))


def _install_estimate_controls(*, patches: ExitStack, memory: SQLiteMemory) -> None:
    from fastapi import HTTPException

    from pyrit.backend.main import app
    from pyrit.backend.services.scenario_service import get_scenario_service
    from pyrit.common.path import CONFIGURATION_DIRECTORY_PATH
    from pyrit.registry import ScenarioPresetRegistry

    isolation = {
        "fixture_root": str(Path(memory.db_path).resolve().parents[1]),
        "database_path": str(Path(memory.db_path).resolve()),
        "configuration_root": str(CONFIGURATION_DIRECTORY_PATH.resolve()),
        "preset_source": ScenarioPresetRegistry.get_registry_singleton().source,
    }
    service = get_scenario_service()
    original_estimate = service.estimate_scenario_run_size_async
    hold_next = False
    fail_next = False
    waiting = False
    release = asyncio.Event()
    release.set()

    async def control_async(command: EstimateControl) -> dict[str, bool | dict[str, str]]:
        nonlocal hold_next, fail_next
        if command.action == "reset":
            hold_next = fail_next = False
            release.set()
        elif command.action == "hold":
            hold_next = True
            release.clear()
        elif command.action == "release":
            release.set()
        elif command.action == "fail":
            fail_next = True
        return {"waiting": waiting, "isolation": isolation}

    async def estimate_async(
        *, scenario_name: str, request: ScenarioRunSizeEstimateRequest
    ) -> ScenarioRunSizeEstimate | None:
        nonlocal hold_next, fail_next, waiting
        if scenario_name.startswith("technique_"):
            if hold_next:
                hold_next = False
                waiting = True
                try:
                    await release.wait()
                finally:
                    waiting = False
            if fail_next:
                fail_next = False
                raise HTTPException(
                    status_code=503, detail="Deliberate offline estimate failure. Try another estimate."
                )
        return await original_estimate(scenario_name=scenario_name, request=request)

    patches.enter_context(patch.object(service, "estimate_scenario_run_size_async", estimate_async))
    app.add_api_route("/api/e2e/techniques/estimate-control", control_async, methods=["POST"])


if __name__ == "__main__":
    with ExitStack() as patches:
        original_serve = analytics_backend._serve

        def _serve(*, directory: Path, port: int) -> None:
            from pyrit.common import path as paths
            from pyrit.registry import ScenarioRegistry

            # Only the three explicitly registered local scenarios belong to this deployment.
            with (
                patch.dict(os.environ, {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "true"}),
                patch.object(paths, "CONFIGURATION_DIRECTORY_PATH", directory / ".pyrit"),
                patch.object(paths, "DEFAULT_CONFIG_PATH", directory / ".pyrit" / paths.DEFAULT_CONFIG_FILENAME),
                patch.object(ScenarioRegistry, "_discover", return_value=None),
            ):
                _guard_preset_paths(patches=patches, directory=directory)
                original_serve(directory=directory, port=port)

        async def _seed_async(memory: SQLiteMemory) -> None:
            from pyrit.registry import ScenarioPresetRegistry

            memory.disable_embedding()
            await asyncio.to_thread(_register_components)
            # Preset writes, like the database, must stay inside the disposable fixture.
            presets = ScenarioPresetRegistry.get_registry_singleton()
            presets.configure_source(str(Path(memory.db_path).parent / "scenario_presets"))
            if not Path(presets.source).resolve().is_relative_to(Path(memory.db_path).resolve().parents[1]):
                raise RuntimeError("The preset source must be inside the fixture before accepting requests.")
            _install_estimate_controls(patches=patches, memory=memory)

        patches.enter_context(patch.object(analytics_backend, "_seed_async", _seed_async))
        patches.enter_context(patch.object(analytics_backend, "_serve", _serve))
        analytics_backend.main()
