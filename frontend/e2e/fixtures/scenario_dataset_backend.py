# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Real Scenario configuration, preset, estimate, and launch routes over offline fixture data."""

from __future__ import annotations

import asyncio
import os
import random
from contextlib import ExitStack
from contextvars import ContextVar
from datetime import UTC
from enum import Enum
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from pydantic import BaseModel

import pyrit.score
from frontend.e2e.fixtures import analytics_backend

if TYPE_CHECKING:
    from pathlib import Path

    from pyrit.backend.models.datasets import DatasetListResponse
    from pyrit.memory import SQLiteMemory
    from pyrit.models.catalog import (
        ScenarioPreset,
        ScenarioRunSizeEstimate,
        ScenarioRunSizeEstimateRequest,
        StoredPreset,
    )
    from pyrit.models.scenario_dataset import DatasetFetchPolicy
    from pyrit.scenario.core.scenario_context import ScenarioContext


class FixtureAction(str, Enum):
    """Explicit, one-shot faults used only by the isolated acceptance server."""

    RESET = "reset"
    STATUS = "status"
    HOLD_ESTIMATE = "hold_estimate"
    RELEASE_ESTIMATE = "release_estimate"
    FAIL_ESTIMATE = "fail_estimate"
    HOLD_METADATA = "hold_metadata"
    RELEASE_METADATA = "release_metadata"
    FAIL_METADATA = "fail_metadata"


class FixtureControl(BaseModel):
    """A control command, not a replacement estimate or dataset response."""

    action: FixtureAction


def _register_components(sent: list[str]) -> None:
    from pyrit.common import apply_defaults
    from pyrit.common.async_compatibility import run_legacy_sync_async
    from pyrit.executor.attack import PromptSendingAttack, RedTeamingAttack
    from pyrit.models import Message, MessagePiece
    from pyrit.prompt_target import PromptTarget, TargetCapabilities, TargetConfiguration
    from pyrit.registry import AttackTechniqueRegistry, ScenarioRegistry, TargetRegistry
    from pyrit.scenario import DatasetAttackConfiguration, DatasetSource, Scenario
    from pyrit.scenario.core import AtomicAttack, BaselineAttackPolicy, ScenarioTechnique
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.matrix_atomic_attack_builder import build_matrix_atomic_attacks
    from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark

    class LocalDatasetTarget(PromptTarget):
        _DEFAULT_CONFIGURATION = TargetConfiguration(
            capabilities=TargetCapabilities(
                supports_multi_turn=True,
                supports_multi_message_pieces=True,
                supports_system_prompt=True,
                supports_editable_history=True,
            )
        )

        async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
            message = normalized_conversation[-1]
            sent.append(message.get_value())
            return [
                MessagePiece(
                    role="assistant",
                    original_value="Offline acceptance response.",
                    conversation_id=message.message_pieces[0].conversation_id,
                ).to_message()
            ]

        def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
            if not normalized_conversation:
                raise ValueError("The offline target requires a message.")

    class DatasetTechnique(ScenarioTechnique):
        ALL = ("all", {"all"})
        DIRECT = ("dataset_direct", set())

        @classmethod
        def get_aggregate_tags(cls) -> set[str]:
            return {"all"}

    class DatasetAcceptanceScenario(Scenario):
        """Harmless named populations using the standard Scenario dataset and estimate machinery."""

        VERSION = 1
        BASELINE_ATTACK_POLICY = BaselineAttackPolicy.Disabled

        @apply_defaults
        def __init__(
            self, *, objective_scorer: pyrit.score.TrueFalseScorer | None = None, scenario_result_id: str | None = None
        ) -> None:
            super().__init__(
                version=self.VERSION,
                technique_class=DatasetTechnique,
                objective_scorer=objective_scorer or pyrit.score.SubStringScorer(substring="Offline acceptance"),
                default_dataset_config=DatasetAttackConfiguration(
                    sources=[
                        DatasetSource(name="acceptance_primary", max_size=2),
                        DatasetSource(name="acceptance_secondary"),
                    ],
                    max_per_dataset=4,
                    max_total=5,
                ),
                scenario_result_id=scenario_result_id,
            )

        async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
            return await run_legacy_sync_async(
                build_matrix_atomic_attacks, context=context, objective_scorer=self._objective_scorer
            )

    class DatasetCacheBenchmark(AdversarialBenchmark):
        """The production benchmark's prior-results range, with a local scorer."""

        @apply_defaults
        def __init__(self, *, scenario_result_id: str | None = None) -> None:
            super().__init__(
                objective_scorer=pyrit.score.SubStringScorer(substring="Offline acceptance"),
                scenario_result_id=scenario_result_id,
            )

    techniques = AttackTechniqueRegistry.get_registry_singleton()
    techniques.register_from_factories(
        [
            AttackTechniqueFactory(name="dataset_direct", attack_class=PromptSendingAttack),
            AttackTechniqueFactory(name="dataset_adversarial", attack_class=RedTeamingAttack),
        ]
    )
    targets = TargetRegistry.get_registry_singleton()
    targets.instances.register(LocalDatasetTarget(), name="Local dataset target")
    scenarios = ScenarioRegistry.get_registry_singleton()
    scenarios.register_class(DatasetAcceptanceScenario, name="offline.datasets")
    scenarios.register_class(DatasetCacheBenchmark, name="offline.dataset_cache")


def _configure_preset_storage(*, database_path: str, patches: ExitStack, mutations: list[dict[str, str]]) -> None:
    from pathlib import Path

    from pyrit.registry import ScenarioPresetRegistry
    from pyrit.registry.scenario_preset_storage import ScenarioPresetStorage

    root = Path(database_path).resolve().parent.parent
    registry = ScenarioPresetRegistry.get_registry_singleton()
    registry.configure_source(str(root / "scenario_presets"))
    if not Path(registry.source).resolve().is_relative_to(root):
        raise RuntimeError("Refusing to use anything except the fixture-owned preset directory.")
    original_save = ScenarioPresetStorage.save_preset
    original_delete = ScenarioPresetStorage.delete_preset

    def guard(storage: ScenarioPresetStorage, *, name: str, action: str) -> None:
        source = Path(storage.display_source).resolve()
        document_path = Path(storage.get_preset_source(name))
        document = document_path.resolve()
        lock = document_path.with_name(f".{document_path.name}{storage.LOCK_SUFFIX}").resolve()
        if not all(path.is_relative_to(root) for path in (source, document, lock)):
            raise RuntimeError("A preset mutation attempted to leave the fixture-owned directory.")
        mutations.append({"action": action, "source": str(source), "document": str(document), "lock": str(lock)})

    def save(self: ScenarioPresetStorage, *, preset: ScenarioPreset, expected_version: str | None) -> StoredPreset:
        guard(self, name=preset.name, action="save")
        return original_save(self, preset=preset, expected_version=expected_version)

    def delete(self: ScenarioPresetStorage, name: str) -> bool:
        guard(self, name=name, action="delete")
        return original_delete(self, name)

    patches.enter_context(patch.object(ScenarioPresetStorage, "save_preset", save))
    patches.enter_context(patch.object(ScenarioPresetStorage, "delete_preset", delete))


def _install_controls(
    *, memory: SQLiteMemory, patches: ExitStack, sent: list[str], preset_mutations: list[dict[str, str]]
) -> None:
    from fastapi import HTTPException

    from pyrit.backend.main import app
    from pyrit.backend.services.dataset_service import DatasetService
    from pyrit.backend.services.scenario_service import ScenarioService
    from pyrit.common import path as paths
    from pyrit.datasets import SeedDatasetProvider
    from pyrit.registry import ScenarioPresetRegistry
    from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
    from pyrit.setup import configuration_loader

    estimating = ContextVar("dataset_acceptance_estimating", default=False)
    original_estimate = ScenarioService.estimate_scenario_run_size_async
    original_metadata = DatasetService.list_datasets_async
    original_prepare = DatasetConfiguration.prepare_async
    estimate_gate = asyncio.Event()
    metadata_gate = asyncio.Event()
    estimate_gate.set()
    metadata_gate.set()
    hold_estimate = hold_metadata = fail_estimate = fail_metadata = False
    estimate_waiting = metadata_waiting = False
    preparations = provider_lookups = 0
    estimates: list[dict[str, Any]] = []

    async def control_async(command: FixtureControl) -> dict[str, Any]:
        nonlocal hold_estimate, hold_metadata, fail_estimate, fail_metadata, preparations, provider_lookups
        action = command.action
        if action is FixtureAction.RESET:
            random.seed(612)
            hold_estimate = hold_metadata = fail_estimate = fail_metadata = False
            preparations = provider_lookups = 0
            estimates.clear()
            sent.clear()
            preset_mutations.clear()
            estimate_gate.set()
            metadata_gate.set()
        elif action is FixtureAction.HOLD_ESTIMATE:
            hold_estimate = True
            estimate_gate.clear()
        elif action is FixtureAction.RELEASE_ESTIMATE:
            estimate_gate.set()
        elif action is FixtureAction.FAIL_ESTIMATE:
            fail_estimate = True
        elif action is FixtureAction.HOLD_METADATA:
            hold_metadata = True
            metadata_gate.clear()
        elif action is FixtureAction.RELEASE_METADATA:
            metadata_gate.set()
        elif action is FixtureAction.FAIL_METADATA:
            fail_metadata = True
        return {
            "estimate_waiting": estimate_waiting,
            "metadata_waiting": metadata_waiting,
            "preparations": preparations,
            "provider_lookups": provider_lookups,
            "sent": list(sent),
            "preset_source": ScenarioPresetRegistry.get_registry_singleton().source,
            "preset_mutations": list(preset_mutations),
            "database_path": str(memory.db_path),
            "configuration_paths": [
                str(paths.CONFIGURATION_DIRECTORY_PATH),
                str(paths.DEFAULT_CONFIG_PATH),
                str(configuration_loader.DEFAULT_CONFIG_PATH),
                os.environ["PYRIT_CONFIG_FILE"],
            ],
            "estimates": list(estimates),
            "stored": [
                {"name": item.dataset_name, "count": item.logical_examples}
                for item in await memory.get_seed_dataset_summaries_async()
            ],
        }

    async def estimate_async(
        self: ScenarioService, *, scenario_name: str, request: ScenarioRunSizeEstimateRequest
    ) -> ScenarioRunSizeEstimate | None:
        nonlocal hold_estimate, fail_estimate, estimate_waiting
        if hold_estimate:
            hold_estimate = False
            estimate_waiting = True
            try:
                await estimate_gate.wait()
            finally:
                estimate_waiting = False
        if fail_estimate:
            fail_estimate = False
            raise HTTPException(status_code=503, detail="Deliberate offline estimate failure.")
        token = estimating.set(True)
        try:
            result = await original_estimate(self, scenario_name=scenario_name, request=request)
            estimates.append(
                {
                    "scenario": scenario_name,
                    "request": request.model_dump(mode="json", exclude_none=True),
                    "response": result.model_dump(mode="json") if result is not None else None,
                }
            )
            return result
        finally:
            estimating.reset(token)

    async def metadata_async(self: DatasetService, *, loaded_only: bool = False) -> DatasetListResponse:
        nonlocal hold_metadata, fail_metadata, metadata_waiting
        if hold_metadata:
            hold_metadata = False
            metadata_waiting = True
            try:
                await metadata_gate.wait()
            finally:
                metadata_waiting = False
        if fail_metadata:
            fail_metadata = False
            raise HTTPException(status_code=503, detail="Deliberate offline dataset metadata failure.")
        return await original_metadata(self, loaded_only=loaded_only)

    async def prepare_async(self: DatasetConfiguration, *, fetch: DatasetFetchPolicy | None = None) -> None:
        nonlocal preparations
        if estimating.get():
            raise AssertionError("A Scenario estimate attempted to prepare datasets.")
        preparations += 1
        await original_prepare(self, fetch=fetch)

    async def providers_async(*, dataset_names: list[str]) -> dict[str, SeedDatasetProvider]:
        nonlocal provider_lookups
        provider_lookups += 1
        raise AssertionError(f"The offline acceptance server attempted provider lookup: {dataset_names}")

    patches.enter_context(patch.object(ScenarioService, "estimate_scenario_run_size_async", estimate_async))
    patches.enter_context(patch.object(DatasetService, "list_datasets_async", metadata_async))
    patches.enter_context(patch.object(DatasetConfiguration, "prepare_async", prepare_async))
    patches.enter_context(patch.object(SeedDatasetProvider, "get_providers_by_name_async", providers_async))
    app.add_api_route("/api/e2e/scenario-datasets", control_async, methods=["POST"])


async def _seed_async(*, memory: SQLiteMemory, patches: ExitStack) -> None:
    from datetime import datetime
    from uuid import UUID

    from pyrit.models import SeedObjective, SeedOrigin

    preset_mutations: list[dict[str, str]] = []
    await asyncio.to_thread(
        _configure_preset_storage, database_path=str(memory.db_path), patches=patches, mutations=preset_mutations
    )
    memory.disable_embedding()
    await memory.add_seeds_to_memory_async(
        seeds=[
            SeedObjective(
                id=UUID(int=0xD000 + dataset_index * 256 + index),
                prompt_group_id=UUID(int=0xD000 + dataset_index * 256 + index),
                date_added=datetime(2025, 1, 1, tzinfo=UTC),
                value=f"Say hello for {name} example {index + 1}.",
                dataset_name=name,
                harm_categories=["Privacy" if index % 2 == 0 else "Cybersecurity"],
                origin=SeedOrigin.LOCAL,
            )
            for dataset_index, (name, count) in enumerate(
                [("acceptance_primary", 8), ("acceptance_secondary", 6), ("acceptance_searchable", 3)], start=1
            )
            for index in range(count)
        ],
        added_by="offline-dataset-acceptance",
    )
    sent: list[str] = []
    _register_components(sent)
    _install_controls(memory=memory, patches=patches, sent=sent, preset_mutations=preset_mutations)


if __name__ == "__main__":
    from pyrit.datasets import SeedDatasetProvider
    from pyrit.registry import ScenarioRegistry

    with ExitStack() as patches:
        # Restrict discovery, not handlers, to fixture-owned populations and scenarios.
        patches.enter_context(patch.object(SeedDatasetProvider, "_registry", {}))
        patches.enter_context(patch.object(SeedDatasetProvider, "_materialize_builtin_providers"))
        patches.enter_context(patch.object(ScenarioRegistry, "_discover"))

        async def seed_fixture_async(memory: SQLiteMemory) -> None:
            await _seed_async(memory=memory, patches=patches)

        original_serve = analytics_backend._serve

        def serve_fixture(*, directory: Path, port: int) -> None:
            from pathlib import Path

            from pyrit.common import path as paths

            configuration = directory / ".pyrit"
            default_config = configuration / paths.DEFAULT_CONFIG_FILENAME

            with (
                patch.dict(os.environ, {"PYRIT_ALLOW_UNAUTHENTICATED_ADMIN": "true"}),
                patch.object(paths, "CONFIGURATION_DIRECTORY_PATH", configuration),
                patch.object(paths, "DEFAULT_CONFIG_PATH", default_config),
                patch.object(paths, "DB_DATA_PATH", directory / "dbdata"),
                patch.object(paths, "PYRIT_CACHE_PATH", directory),
            ):
                from pyrit.setup import configuration_loader

                with patch.object(configuration_loader, "DEFAULT_CONFIG_PATH", default_config):
                    if not all(
                        path.resolve().is_relative_to(directory)
                        for path in (
                            paths.CONFIGURATION_DIRECTORY_PATH,
                            paths.DEFAULT_CONFIG_PATH,
                            configuration_loader.DEFAULT_CONFIG_PATH,
                            Path(os.environ["PYRIT_CONFIG_FILE"]),
                        )
                    ):
                        raise RuntimeError("Refusing to start with configuration outside the fixture-owned directory.")
                    original_serve(directory=directory, port=port)

        patches.enter_context(patch.object(analytics_backend, "_seed_async", seed_fixture_async))
        patches.enter_context(patch.object(analytics_backend, "_serve", serve_fixture))
        analytics_backend.main()
