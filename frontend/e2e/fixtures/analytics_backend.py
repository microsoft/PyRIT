# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Real analytics routes and bundled UI over disposable, offline SQLite data."""

from __future__ import annotations

import os
import tempfile
import uuid
from argparse import ArgumentParser
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI

    from pyrit.memory import SQLiteMemory


async def _seed_async(memory: SQLiteMemory) -> None:
    from pyrit.models import (
        AtomicAttackIdentifier,
        AttackIdentifier,
        AttackOutcome,
        AttackResult,
        AttackResultMetadata,
        AttackResultRole,
        Conversation,
        ConverterIdentifier,
        MessagePiece,
        Operation,
        ScenarioIdentifier,
        ScenarioResult,
        ScenarioRunState,
        TargetIdentifier,
    )

    memory.disable_embedding()
    start = datetime(2026, 10, 12, 18, tzinfo=UTC)
    scenario_id = uuid.UUID(int=9000)
    outcomes = [
        "failure",
        "success",
        "success",
        "success",
        "failure",
        "success",
        "error",
        "undetermined",
        "undetermined",
        "undetermined",
    ]
    harms = ["Privacy", "Misinformation", "Cybersecurity"]
    attack_names = ["PromptSendingAttack", "RedTeamingAttack", "CrescendoAttack"]
    baseline = await memory.add_operation_async(Operation(id=uuid.UUID(int=0xA001), name="Synthetic baseline"))
    pilot = await memory.add_operation_async(Operation(id=uuid.UUID(int=0xA002), name="Synthetic pilot"))
    results: list[AttackResult] = []
    for index in range(204):
        configuration = (index // 2) % 3
        target = TargetIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            model_name=f"Synthetic model {chr(65 + configuration)}",
        )
        request_converters = (
            []
            if configuration == 2
            else [
                ConverterIdentifier(class_name="Base64Converter", class_module="pyrit.converter"),
            ]
        )
        if configuration == 1:
            request_converters.append(ConverterIdentifier(class_name="ROT13Converter", class_module="pyrit.converter"))
        identifier = AtomicAttackIdentifier.build(
            attack_identifier=AttackIdentifier(
                class_name=attack_names[configuration],
                class_module="pyrit.executor.attack",
                objective_target=target,
                request_converters=request_converters,
                response_converters=[]
                if configuration != 1
                else [
                    ConverterIdentifier(class_name="StringJoinConverter", class_module="pyrit.converter"),
                ],
            ),
        )
        conversation_id = str(uuid.UUID(int=1000 + index // 2))
        if index % 2 == 0:
            await memory.add_conversation_to_memory_async(
                conversation=Conversation(
                    conversation_id=conversation_id,
                    target_identifier=target,
                    attack_result_id=str(uuid.UUID(int=index + 1)),
                )
            )
            await memory.add_message_pieces_to_memory_async(
                message_pieces=[
                    MessagePiece(
                        role="user",
                        conversation_id=conversation_id,
                        sequence=0,
                        original_value=f"Synthetic evaluation example {index // 2 + 1}. No external model was called.",
                    ),
                    MessagePiece(
                        role="assistant",
                        conversation_id=conversation_id,
                        sequence=1,
                        original_value="This is a safe, locally stored example response for analytics browser checks.",
                    ),
                ]
            )
        operation = baseline if index < 80 else pilot if index < 120 else None
        legacy_operation = (
            None if operation else "Synthetic follow-up" if index < 144 else f"Synthetic batch {index - 144:02}"
        )
        role = (AttackResultRole.TARGET_FACING, AttackResultRole.ORCHESTRATION, AttackResultRole.UNKNOWN)[index % 3]
        results.append(
            AttackResult(
                attack_result_id=str(uuid.UUID(int=index + 1)),
                conversation_id=conversation_id,
                objective=f"Synthetic {harms[configuration].lower()} check {index // 2 + 1}",
                outcome=AttackOutcome(outcomes[index % len(outcomes)]),
                atomic_attack_identifier=None if index % 19 == 18 else identifier,
                operator=("Alex (synthetic)", "Sam (synthetic)", "Jordan (synthetic)")[configuration],
                operation=legacy_operation,
                operation_id=operation.id if operation else None,
                timestamp=start + timedelta(minutes=index),
                targeted_harm_categories=[harms[configuration]]
                + ([harms[(configuration + 1) % 3]] if index % 5 == 0 else []),
                labels={
                    "source": "analytics-browser-fixture",
                    "team": ("research", "assurance")[index % 2],
                    "cohort": "overview" if index < 144 else "facet-pagination",
                    **({"long-a": "x" * 4096, "long-b": "y" * 4096} if index < 2 else {}),
                },
                attribution_parent_id=str(scenario_id) if index < 2 else None,
                attribution_data=None
                if role is AttackResultRole.UNKNOWN
                else AttackResultMetadata(
                    result_role=role,
                    attempt_index=index % 2,
                ).to_metadata(),
            )
        )
    await memory.add_attack_results_to_memory_async(attack_results=results)
    await memory.add_scenario_results_to_memory_async(
        scenario_results=[
            ScenarioResult(
                id=scenario_id,
                scenario_identifier=ScenarioIdentifier(
                    class_name="SyntheticRetryScenario",
                    class_module="analytics_browser_fixture",
                    version=1,
                    objective_target=TargetIdentifier(
                        class_name="OpenAIChatTarget",
                        class_module="pyrit.prompt_target",
                        model_name="Synthetic model A",
                    ),
                ),
                scenario_description="Synthetic failed attempt followed by success, sharing one conversation.",
                attack_results={"PromptSendingAttack": results[:2]},
                scenario_run_state=ScenarioRunState.COMPLETED,
                creation_time=start,
                completion_time=start + timedelta(minutes=1),
            )
        ]
    )


def _serve(*, directory: Path, port: int) -> None:
    import uvicorn

    from pyrit.common import path as paths

    data = directory / "dbdata"
    data.mkdir()
    # Source checkouts otherwise default to <worktree>/dbdata, regardless of HOME.
    with patch.object(paths, "DB_DATA_PATH", data), patch.object(paths, "PYRIT_CACHE_PATH", directory):
        from pyrit.backend.main import SPAStaticFiles, app, lifespan
        from pyrit.memory import CentralMemory, SQLiteMemory

        frontend = Path(__file__).resolve().parents[2] / "dist"
        if not (frontend / "index.html").is_file():
            raise RuntimeError("Build the frontend before starting the analytics browser fixture.")

        @asynccontextmanager
        async def fixture_lifespan_async(application: FastAPI) -> AsyncGenerator[None, None]:
            async with lifespan(application):
                if application.state.runtime_lifecycle.state != "ready":
                    raise RuntimeError(application.state.runtime_lifecycle.message)
                memory = CentralMemory.get_memory_instance()
                if not isinstance(memory, SQLiteMemory) or not Path(memory.db_path).resolve().is_relative_to(directory):
                    raise RuntimeError("Refusing to seed anything except the fixture-owned SQLite database.")
                await _seed_async(memory)
                application.mount("/", SPAStaticFiles(directory=str(frontend), html=True), name="analytics-browser-ui")
                print(f"Analytics browser fixture: {memory.db_path}", flush=True)
                yield

        app.router.lifespan_context = fixture_lifespan_async
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    """Serve only fixture-owned data with user configuration discovery disabled."""
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18496)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    scratch = root / ".pyrit_cache"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="analytics-e2e-", dir=scratch) as temporary:
        directory = Path(temporary).resolve()
        config = directory / "config.yaml"
        config.write_text(
            "memory_db_type: sqlite\nenv_files: []\nenv_akv_ref: []\ninitializers: []\n"
            "initialization_scripts: []\nsilent: true\noperator: synthetic\noperation: synthetic\n",
            encoding="utf-8",
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PYTHONIOENCODING"}
        }
        environment.update(
            {
                "HOME": str(directory),
                "USERPROFILE": str(directory),
                "APPDATA": str(directory),
                "LOCALAPPDATA": str(directory),
                "XDG_CONFIG_HOME": str(directory),
                "XDG_DATA_HOME": str(directory),
                "PYRIT_AUTH_MODE": "local",
                "PYRIT_CONFIG_FILE": str(config),
                "PYRIT_DEV_MODE": "true",
            }
        )
        with patch.dict(os.environ, environment, clear=True):
            _serve(directory=directory, port=args.port)


if __name__ == "__main__":
    main()
