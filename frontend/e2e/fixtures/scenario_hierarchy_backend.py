# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Disposable saved Scenario run with compound results for hierarchy browser acceptance tests."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from unittest.mock import patch
from uuid import UUID

from pydantic import BaseModel

from frontend.e2e.fixtures import analytics_backend

if TYPE_CHECKING:
    from pyrit.memory import SQLiteMemory
    from pyrit.memory.memory_interface import (
        AttackConversationPage,
        ConversationMessagePage,
        MessagePieceKeysetCursor,
    )
    from pyrit.models import MessagePiece

RUN_ID = UUID(int=0x3602)
CHILD_COUNT = 520
MISSING_POSITION = 1
DUPLICATE_POSITION = 3
SAVED_ATTACK_ID = str(UUID(int=0x36020000E))
SAVED_MAIN_ID = str(UUID(int=0x36030000E))
SAVED_PRUNED_COUNT = 120


class SavedEvidenceControl(BaseModel):
    """One-shot offline read faults, never provider or production configuration."""

    action: Literal[
        "reset",
        "status",
        "fail_messages",
        "fail_references",
        "hold_messages",
        "release_messages",
        "hold_references",
        "release_references",
    ]
    status: Literal[400, 503] = 503


def _media_piece(*, directory: Path, timestamp: datetime) -> MessagePiece:
    from PIL import Image

    from pyrit.models import ConverterIdentifier, MessagePiece

    media_directory = directory / "prompt-memory-entries"
    media_directory.mkdir(exist_ok=True)
    original = media_directory / "saved-evidence-original.png"
    converted = media_directory / "saved-evidence-converted.png"
    for path, color in [(original, "red"), (converted, "green")]:
        with Image.new("RGB", (8, 8), color) as image:
            image.save(path)
    return MessagePiece(
        id=UUID(int=0x10BFFFF),
        role="assistant",
        conversation_id=SAVED_MAIN_ID,
        sequence=1,
        timestamp=timestamp + timedelta(seconds=1, microseconds=124000),
        original_value_data_type="image_path",
        original_value=str(original),
        converted_value_data_type="image_path",
        converted_value=str(converted),
        converter_identifiers=[
            ConverterIdentifier(class_name="OfflineImageConverter", class_module="hierarchy_browser_fixture")
        ],
        prompt_metadata={"source": "offline-saved-evidence"},
    )


async def _long_evidence_async(*, directory: Path, timestamp: datetime) -> list[MessagePiece]:
    from pyrit.models import MessagePiece

    return [
        MessagePiece(
            role="system",
            conversation_id=SAVED_MAIN_ID,
            sequence=-1,
            timestamp=timestamp,
            original_value="Saved system context for Step 5. This is offline evidence.",
        ),
        *[
            MessagePiece(
                role="assistant",
                conversation_id=SAVED_MAIN_ID,
                sequence=1,
                timestamp=timestamp + timedelta(seconds=1, milliseconds=index),
                original_value=f"Saved response piece {index:02d}.",
            )
            for index in range(1, 97)
        ],
        MessagePiece(
            id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
            role="assistant",
            conversation_id=SAVED_MAIN_ID,
            sequence=1,
            timestamp=timestamp + timedelta(seconds=1, microseconds=123100),
            original_value="Earlier microsecond piece.",
        ),
        MessagePiece(
            id=UUID(int=0),
            role="assistant",
            conversation_id=SAVED_MAIN_ID,
            sequence=1,
            timestamp=timestamp + timedelta(seconds=1, microseconds=123900),
            original_value="Later microsecond piece.",
        ),
        await asyncio.to_thread(_media_piece, directory=directory, timestamp=timestamp),
        *[
            MessagePiece(
                role="user" if sequence % 2 == 0 else "assistant",
                conversation_id=SAVED_MAIN_ID,
                sequence=sequence,
                timestamp=timestamp + timedelta(seconds=sequence),
                original_value=f"Saved continuation turn {sequence}. Offline evidence.",
            )
            for sequence in range(2, 122)
        ],
    ]


def _install_read_controls(*, memory: SQLiteMemory, patches: ExitStack) -> None:
    from fastapi import HTTPException

    from pyrit.backend.main import app
    from pyrit.models import Message

    message_error = 0
    reference_error = 0
    hold_next_message = False
    hold_next_reference = False
    waiting = False
    reference_waiting = False
    release = asyncio.Event()
    release.set()
    reference_release = asyncio.Event()
    reference_release.set()
    original_messages = memory.get_conversation_message_page_async
    original_references = memory.get_attack_conversation_page_async

    async def control_async(command: SavedEvidenceControl) -> dict[str, bool]:
        nonlocal message_error, reference_error, hold_next_message, hold_next_reference
        if command.action == "reset":
            message_error = reference_error = 0
            hold_next_message = hold_next_reference = False
            release.set()
            reference_release.set()
        elif command.action == "fail_messages":
            message_error = command.status
        elif command.action == "fail_references":
            reference_error = command.status
        elif command.action == "hold_messages":
            hold_next_message = True
            release.clear()
        elif command.action == "release_messages":
            release.set()
        elif command.action == "hold_references":
            hold_next_reference = True
            reference_release.clear()
        elif command.action == "release_references":
            reference_release.set()
        return {"waiting": waiting, "reference_waiting": reference_waiting}

    async def messages_async(
        *, conversation_id: str, cursor: MessagePieceKeysetCursor | None = None, limit: int = 100
    ) -> ConversationMessagePage:
        nonlocal message_error, hold_next_message, waiting
        if conversation_id == SAVED_MAIN_ID:
            if message_error:
                status = message_error
                message_error = 0
                raise HTTPException(status_code=status, detail="Offline saved message read fault")
            if cursor is not None and hold_next_message:
                hold_next_message = False
                waiting = True
                try:
                    await release.wait()
                finally:
                    waiting = False
        # Include an overlapping stable piece without exceeding the requested piece bound.
        overlapping = cursor is not None and limit > 1
        page = await original_messages(conversation_id=conversation_id, cursor=cursor, limit=limit - int(overlapping))
        if overlapping:
            anchors = await memory.get_message_pieces_async(
                conversation_id=conversation_id, prompt_ids=[cursor.message_piece_id]
            )
            if len(anchors) != 1:
                raise RuntimeError("The offline fixture lost its saved-message cursor anchor.")
            page = replace(page, messages=(Message(message_pieces=[anchors[0]]), *page.messages))
        return page

    async def references_async(
        *, attack_result_id: str, offset: int = 0, limit: int = 50, conversation_id: str | None = None
    ) -> AttackConversationPage | None:
        nonlocal reference_error, hold_next_reference, reference_waiting
        if attack_result_id == SAVED_ATTACK_ID:
            if reference_error:
                status = reference_error
                reference_error = 0
                raise HTTPException(status_code=status, detail="Offline saved reference read fault")
            if offset > 0 and hold_next_reference:
                hold_next_reference = False
                reference_waiting = True
                try:
                    await reference_release.wait()
                finally:
                    reference_waiting = False
        return await original_references(
            attack_result_id=attack_result_id, offset=offset, limit=limit, conversation_id=conversation_id
        )

    patches.enter_context(patch.object(memory, "get_conversation_message_page_async", messages_async))
    patches.enter_context(patch.object(memory, "get_attack_conversation_page_async", references_async))
    app.add_api_route("/api/e2e/saved-evidence", control_async, methods=["POST"])


async def _seed_async(memory: SQLiteMemory) -> None:
    from pyrit.common.utils import to_sha256
    from pyrit.models import (
        SCENARIO_RUN_PLAN_METADATA_KEY,
        AtomicAttackIdentifier,
        AttackIdentifier,
        AttackOutcome,
        AttackResult,
        AttackResultRole,
        Conversation,
        ConversationReference,
        ConversationType,
        MessagePiece,
        ScenarioIdentifier,
        ScenarioResult,
        ScenarioRunPlan,
        ScenarioRunPlanAtomicGroup,
        ScenarioRunPlanGroupKind,
        ScenarioRunPlanSeedGroup,
        ScenarioRunState,
        Score,
        ScorerIdentifier,
        TargetIdentifier,
    )

    memory.disable_embedding()
    timestamp = datetime(2026, 10, 28, tzinfo=UTC)
    target = TargetIdentifier(class_name="MockPromptTarget", class_module="hierarchy_browser_fixture")
    identifier = AtomicAttackIdentifier.build(
        attack_identifier=AttackIdentifier(
            class_name="SequentialAttack",
            class_module="pyrit.executor.attack",
            objective_target=target,
        )
    )
    plan = ScenarioRunPlan(
        atomic_groups=[
            ScenarioRunPlanAtomicGroup(
                id=name,
                atomic_attack_name=name,
                display_group=display,
                technique_eval_hash=f"{name}-eval",
                kind=ScenarioRunPlanGroupKind.ATTACK,
                seed_group_ids=["seed"],
            )
            for name, display in [("sequential", "Sequential"), ("single", "Single turn")]
        ],
        seed_groups=[ScenarioRunPlanSeedGroup(id="seed", objective="seed", objective_sha256=to_sha256("seed"))],
    )

    def _result(
        *,
        index: int,
        group: str,
        objective: str,
        outcome: AttackOutcome = AttackOutcome.FAILURE,
        child_ids: list[str] | None = None,
        attempt_index: int | None = None,
    ) -> AttackResult:
        return AttackResult(
            attack_result_id=str(UUID(int=0x360200000 + index)),
            conversation_id="" if child_ids is not None else str(UUID(int=0x360300000 + index)),
            objective=objective,
            outcome=outcome,
            timestamp=timestamp + timedelta(seconds=index),
            atomic_attack_identifier=identifier,
            attribution_parent_id=str(RUN_ID),
            attribution_data={
                "seed_group_id": "seed",
                "parent_eval_hash": f"{group}-eval",
                "parent_collection": group,
                # Like SequentialAttack: children share the parent's attribution plus their position.
                **({"attempt_index": attempt_index} if attempt_index is not None else {}),
                "result_role": (
                    AttackResultRole.ORCHESTRATION.value
                    if child_ids is not None
                    else AttackResultRole.TARGET_FACING.value
                ),
            },
            metadata={"child_attack_result_ids": child_ids} if child_ids is not None else {},
        )

    grandchildren = [
        _result(index=1000 + i, group="sequential", objective=f"Nested step {i + 1}", attempt_index=i + 1)
        for i in range(2)
    ]
    children = [
        _result(
            index=10 + i,
            group="sequential",
            objective=f"Step {i + 1}",
            outcome=AttackOutcome.SUCCESS if i == 4 else AttackOutcome.FAILURE,
            child_ids=[g.attack_result_id for g in grandchildren] if i == 0 else None,
            attempt_index=i + 1,
        )
        for i in range(CHILD_COUNT)
    ]
    child_ids = [child.attack_result_id for child in children]
    child_ids[MISSING_POSITION] = str(UUID(int=0x3602DEAD))
    child_ids[DUPLICATE_POSITION] = child_ids[0]
    parent = _result(index=1, group="sequential", objective="Sequential parent", child_ids=child_ids)
    single = _result(index=2, group="single", objective="Single-turn attempt", outcome=AttackOutcome.SUCCESS)
    saved = children[4]
    saved.related_conversations = {
        ConversationReference(
            conversation_id=str(UUID(int=0x10B000000 + index)), conversation_type=ConversationType.PRUNED
        )
        for index in range(1, SAVED_PRUNED_COUNT + 1)
    }

    rows = {"sequential": [parent, *children, *grandchildren], "single": [single]}
    await memory.add_attack_results_to_memory_async(attack_results=[row for group in rows.values() for row in group])
    pieces: list[MessagePiece] = []
    for group in rows.values():
        for row in group:
            if not row.conversation_id:
                continue
            await memory.add_conversation_to_memory_async(
                conversation=Conversation(
                    conversation_id=row.conversation_id,
                    target_identifier=target,
                    attack_result_id=row.attack_result_id,
                )
            )
            pieces.extend(
                [
                    MessagePiece(
                        role="user",
                        conversation_id=row.conversation_id,
                        sequence=0,
                        timestamp=timestamp,
                        original_value=f"Synthetic request for {row.objective}. No external model was called.",
                    ),
                    MessagePiece(
                        role="assistant",
                        conversation_id=row.conversation_id,
                        sequence=1,
                        timestamp=timestamp + timedelta(seconds=1),
                        original_value=f"Stored example response for {row.objective}. This is offline sample data.",
                    ),
                ]
            )
            if row.attack_result_id == SAVED_ATTACK_ID:
                saved_evidence = await _long_evidence_async(directory=Path(memory.db_path).parent, timestamp=timestamp)
                pieces.extend(saved_evidence)
                saved.last_response = saved_evidence[-1]
    pruned_roles: tuple[Literal["user", "assistant"], ...] = ("user", "assistant")
    for reference in sorted(saved.related_conversations, key=lambda reference: reference.conversation_id):
        await memory.add_conversation_to_memory_async(
            conversation=Conversation(
                conversation_id=reference.conversation_id, target_identifier=target, attack_result_id=SAVED_ATTACK_ID
            )
        )
        pieces.extend(
            [
                MessagePiece(
                    role=role,
                    conversation_id=reference.conversation_id,
                    sequence=sequence,
                    timestamp=timestamp + timedelta(seconds=sequence),
                    original_value=f"Saved pruned {role} for {reference.conversation_id}. Offline evidence.",
                )
                for sequence, role in enumerate(pruned_roles)
            ]
        )
    await memory.add_message_pieces_to_memory_async(message_pieces=pieces)
    if saved.last_response is None:
        raise RuntimeError("The offline saved attack has no final response to assess.")
    if not await memory.update_attack_result_by_id_async(
        attack_result_id=SAVED_ATTACK_ID, update_fields={"last_response_id": saved.last_response.id}
    ):
        raise RuntimeError("The offline saved attack lost its final response reference.")
    await memory.add_scores_to_memory_async(
        scores=[
            Score(
                id=UUID(int=0x10B5C0),
                message_piece_id=UUID(int=0x10BFFFF),
                score_type="float_scale",
                score_value="0.75",
                score_category=["Offline evidence"],
                score_rationale="Locally stored score for the converted saved image.",
                scorer_class_identifier=ScorerIdentifier(
                    class_name="OfflineEvidenceScorer",
                    class_module="hierarchy_browser_fixture",
                    scorer_type="float_scale",
                ),
                timestamp=timestamp,
            )
        ]
    )
    await memory.add_scenario_results_to_memory_async(
        scenario_results=[
            ScenarioResult(
                id=RUN_ID,
                scenario_identifier=ScenarioIdentifier(
                    class_name="HierarchyBrowserScenario",
                    class_module="hierarchy_browser_fixture",
                    version=1,
                    objective_target=target,
                ),
                scenario_description="Offline compound-result hierarchy acceptance fixture.",
                attack_results=rows,
                metadata={SCENARIO_RUN_PLAN_METADATA_KEY: plan.model_dump(mode="json")},
                scenario_run_state=ScenarioRunState.COMPLETED,
                creation_time=timestamp,
                completion_time=timestamp + timedelta(minutes=1),
            )
        ]
    )


if __name__ == "__main__":
    with ExitStack() as patches:

        async def _controlled_seed_async(memory: SQLiteMemory) -> None:
            await _seed_async(memory)
            _install_read_controls(memory=memory, patches=patches)

        with patch.object(analytics_backend, "_seed_async", _controlled_seed_async):
            analytics_backend.main()
