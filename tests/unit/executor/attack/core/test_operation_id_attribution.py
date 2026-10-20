# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import AttackExecutor, PromptSendingAttack
from pyrit.executor.attack.compound import SequenceCompletionPolicy, SequentialAttack, SequentialChildAttack
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution
from pyrit.executor.attack.core.attack_strategy import AttackContext, AttackStrategy
from pyrit.memory import CentralMemory, MemoryInterface
from pyrit.models import (
    AttackResult,
    AttackResultSelection,
    AttackSeedGroup,
    Message,
    Operation,
    OperationUsage,
    SeedObjective,
)


class AdmissionProbe(AttackStrategy[AttackContext[AttackParameters], AttackResult]):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__(objective_target=MockPromptTarget(), context_type=AttackContext)
        self.setup_count = 0
        self.perform_count = 0
        self.fail = fail

    def _validate_context(self, *, context: AttackContext[AttackParameters]) -> None:
        pass

    async def _setup_async(self, *, context: AttackContext[AttackParameters]) -> None:
        self.setup_count += 1

    async def _teardown_async(self, *, context: AttackContext[AttackParameters]) -> None:
        pass

    async def _perform_async(self, *, context: AttackContext[AttackParameters]) -> AttackResult:
        self.perform_count += 1
        if self.fail:
            raise RuntimeError("Probe failed")
        return AttackResult(conversation_id="probe", objective=context.objective)


@pytest.mark.usefixtures("patch_central_database")
class TestOperationAdmission:
    async def test_unknown_id_rejected_before_setup_or_target_async(self, sqlite_instance: MemoryInterface) -> None:
        attack = AdmissionProbe()
        with pytest.raises(LookupError, match="Operation"):
            await attack.execute_async(objective="objective", operation_id=uuid4())
        assert attack.setup_count == attack.perform_count == 0
        assert await sqlite_instance.get_attack_results_async() == []

    async def test_name_label_links_saved_operation_and_keeps_the_name_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        operation = await sqlite_instance.add_operation_async(Operation(name="Old"))
        attack = PromptSendingAttack(objective_target=MockPromptTarget())
        with pytest.warns(DeprecationWarning, match="operation_id"):
            result = await attack.execute_async(objective="objective", memory_labels={"operation": " OLD", "x": "y"})
        assert result.operation_id == operation.id
        saved = (await sqlite_instance.get_attack_results_async(attack_result_ids=[result.attack_result_id]))[0]
        assert (saved.operation_id, saved.operation, saved.labels) == (operation.id, " OLD", {"x": "y"})
        by_name = await sqlite_instance.get_attack_results_async(operation=[" OLD"])
        by_id = await sqlite_instance.get_attack_results_async(operation_ids=[operation.id])
        assert [found.attack_result_id for found in by_name] == [result.attack_result_id]
        assert [found.attack_result_id for found in by_id] == [result.attack_result_id]

    async def test_unknown_name_is_kept_without_an_operation_id_async(self, sqlite_instance: MemoryInterface) -> None:
        attack = PromptSendingAttack(objective_target=MockPromptTarget())
        with pytest.warns(DeprecationWarning, match="operation_id"):
            result = await attack.execute_async(objective="objective", memory_labels={"operation": "missing"})
        assert result.operation_id is None
        found = await sqlite_instance.get_attack_results_async(operation=["missing"])
        assert [(saved.attack_result_id, saved.operation_id) for saved in found] == [(result.attack_result_id, None)]

    async def test_global_name_applies_only_to_unattributed_standalone_runs_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        global_operation = await sqlite_instance.add_operation_async(Operation(name="Global"))
        chosen = await sqlite_instance.add_operation_async(Operation(name="Chosen"))
        with patch.dict("os.environ", {"GLOBAL_MEMORY_LABELS": '{"operation": "global", "x": "y"}'}):
            attack = AdmissionProbe()
        assert attack._memory_labels == {"x": "y"}
        assert (await attack.execute_async(objective="objective")).operation_id == global_operation.id
        assert (await attack.execute_async(objective="objective", operation_id=chosen.id)).operation_id == chosen.id
        context = AttackContext(params=AttackParameters(objective="objective"))
        context._attribution = AttackResultAttribution(parent_id=str(uuid4()), parent_collection="scenario")
        context._persist_attack_result = False
        assert (await attack.execute_with_context_async(context=context)).operation_id is None

    @pytest.mark.parametrize("persist", [False, True])
    async def test_operation_id_stamped_on_success_async(self, sqlite_instance: MemoryInterface, persist: bool) -> None:
        operation = await sqlite_instance.add_operation_async(Operation(name="Run"))
        attack = AdmissionProbe()
        result = await attack.execute_async(
            objective="objective", operation_id=operation.id, persist_attack_result=persist
        )
        assert result.operation_id == operation.id
        saved = await sqlite_instance.get_attack_results_async(
            attack_result_ids=[result.attack_result_id], result_selection=AttackResultSelection.ALL_RESULTS
        )
        assert len(saved) == int(persist)
        if saved:
            assert saved[0].operation_id == operation.id

    async def test_operation_id_stamped_on_error_async(self, sqlite_instance: MemoryInterface) -> None:
        operation = await sqlite_instance.add_operation_async(Operation(name="Run"))
        attack = AdmissionProbe(fail=True)
        with pytest.raises(RuntimeError, match="Probe failed"):
            await attack.execute_async(objective="objective", operation_id=operation.id)
        saved = await sqlite_instance.get_attack_results_async(result_selection=AttackResultSelection.ALL_RESULTS)
        assert len(saved) == 1
        assert saved[0].operation_id == operation.id

    async def test_compound_children_inherit_id_async(self, sqlite_instance: MemoryInterface) -> None:
        operation = await sqlite_instance.add_operation_async(Operation(name="Compound"))
        target = MockPromptTarget()
        child = PromptSendingAttack(objective_target=target)
        compound = SequentialAttack(
            objective_target=target,
            child_attacks=[
                SequentialChildAttack(
                    strategy=child, seed_group=AttackSeedGroup(seeds=[SeedObjective(value="objective")])
                )
            ],
        )
        result = await compound.execute_async(objective="objective", operation_id=operation.id)
        assert result.operation_id == operation.id
        saved = await sqlite_instance.get_attack_results_async(result_selection=AttackResultSelection.ALL_RESULTS)
        assert len(saved) == 2
        assert all(row.operation_id == operation.id for row in saved)


class _PausedTarget(MockPromptTarget):
    """A target that waits to respond until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.sending = asyncio.Event()
        self.release = asyncio.Event()

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        self.sending.set()
        await self.release.wait()
        return await super()._send_prompt_to_target_async(normalized_conversation=normalized_conversation)


class _DeletingProbe(AdmissionProbe):
    """Deletes the run's Operation from inside the attack, then succeeds or fails."""

    async def _perform_async(self, *, context: AttackContext[AttackParameters]) -> AttackResult:
        operation_id = context.params.operation_id
        assert operation_id is not None
        await CentralMemory.get_memory_instance().delete_operation_async(operation_id=operation_id)
        return await super()._perform_async(context=context)


@pytest.mark.usefixtures("patch_central_database")
class TestOperationDeletedDuringRun:
    async def test_in_flight_attack_keeps_its_result_without_the_deleted_operation_async(
        self, sqlite_instance: MemoryInterface, caplog: pytest.LogCaptureFixture
    ) -> None:
        operation = await sqlite_instance.add_operation_async(Operation(name="Deleted mid-run"))
        target = _PausedTarget()
        running = asyncio.create_task(
            PromptSendingAttack(objective_target=target).execute_async(
                objective="Say hello.", operation_id=operation.id
            )
        )
        await asyncio.wait_for(target.sending.wait(), timeout=10)
        # Nothing refers to the Operation until the result is saved, so it can be deleted mid-run.
        assert await sqlite_instance.get_operation_usage_async(operation_id=operation.id) == OperationUsage(
            findings=0, attacks=0, scenario_runs=0
        )
        await sqlite_instance.delete_operation_async(operation_id=operation.id)
        target.release.set()
        with caplog.at_level(logging.WARNING):
            result = await asyncio.wait_for(running, timeout=10)
        assert result.operation_id is None
        saved = await sqlite_instance.get_attack_results_async(attack_result_ids=[result.attack_result_id])
        assert [row.operation_id for row in saved] == [None]
        assert len(await sqlite_instance.get_message_pieces_async(conversation_id=result.conversation_id)) == 2
        assert str(operation.id) in caplog.text

    @pytest.mark.parametrize("fail", [False, True])
    async def test_success_and_error_results_are_saved_without_the_deleted_operation_async(
        self, sqlite_instance: MemoryInterface, fail: bool
    ) -> None:
        operation = await sqlite_instance.add_operation_async(Operation(name="Deleted mid-run"))
        attack = _DeletingProbe(fail=fail)
        if fail:
            with pytest.raises(RuntimeError, match="Probe failed"):
                await attack.execute_async(objective="objective", operation_id=operation.id)
        else:
            assert (await attack.execute_async(objective="objective", operation_id=operation.id)).operation_id is None
        saved = await sqlite_instance.get_attack_results_async(result_selection=AttackResultSelection.ALL_RESULTS)
        assert [row.operation_id for row in saved] == [None]
        assert await sqlite_instance.get_operations_async() == []

    async def test_other_lookup_errors_still_fail_the_save_async(self, sqlite_instance: MemoryInterface) -> None:
        operation = await sqlite_instance.add_operation_async(Operation(name="Still here"))
        save = AsyncMock(side_effect=LookupError("Atomic attack identifier not found"))
        with patch.object(sqlite_instance, "add_attack_results_to_memory_async", save):
            with pytest.raises(LookupError, match="identifier"):
                await AdmissionProbe().execute_async(objective="objective", operation_id=operation.id)
        save.assert_awaited_once()

    async def test_in_flight_sequence_runs_every_child_without_the_deleted_operation_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        operation = await sqlite_instance.add_operation_async(Operation(name="Deleted mid-sequence"))
        target = _PausedTarget()
        children = [
            SequentialChildAttack(
                strategy=PromptSendingAttack(objective_target=target),
                seed_group=AttackSeedGroup(seeds=[SeedObjective(value=f"Say hello {index}.")]),
            )
            for index in range(2)
        ]
        sequence = SequentialAttack(
            objective_target=target, child_attacks=children, completion_policy=SequenceCompletionPolicy.EXHAUSTIVE
        )
        running = asyncio.create_task(sequence.execute_async(objective="Say hello.", operation_id=operation.id))
        await asyncio.wait_for(target.sending.wait(), timeout=10)
        await sqlite_instance.delete_operation_async(operation_id=operation.id)
        target.release.set()
        result = await asyncio.wait_for(running, timeout=10)
        assert result.operation_id is None
        assert len(target.prompt_sent) == 2
        saved = await sqlite_instance.get_attack_results_async(result_selection=AttackResultSelection.ALL_RESULTS)
        assert len(saved) == 3
        assert all(row.operation_id is None for row in saved)

    async def test_in_flight_batch_runs_every_objective_without_the_deleted_operation_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        operation = await sqlite_instance.add_operation_async(Operation(name="Deleted mid-batch"))
        target = _PausedTarget()
        running = asyncio.create_task(
            AttackExecutor().execute_attack_async(
                attack=PromptSendingAttack(objective_target=target),
                objectives=["Say hello.", "Say goodbye."],
                operation_id=operation.id,
            )
        )
        await asyncio.wait_for(target.sending.wait(), timeout=10)
        await sqlite_instance.delete_operation_async(operation_id=operation.id)
        target.release.set()
        executor_result = await asyncio.wait_for(running, timeout=10)
        assert [result.operation_id for result in executor_result.completed_results] == [None, None]
        assert len(target.prompt_sent) == 2
        saved = await sqlite_instance.get_attack_results_async(result_selection=AttackResultSelection.ALL_RESULTS)
        assert [row.operation_id for row in saved] == [None, None]

    async def test_batch_given_a_missing_operation_still_rejects_every_objective_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        attack = AdmissionProbe()
        executor_result = await AttackExecutor().execute_attack_async(
            attack=attack, objectives=["first", "second"], operation_id=uuid4(), return_partial_on_failure=True
        )
        assert executor_result.completed_results == []
        assert all(isinstance(error, LookupError) for _, error in executor_result.incomplete_objectives)
        assert len(executor_result.incomplete_objectives) == 2
        assert attack.setup_count == attack.perform_count == 0
        assert await sqlite_instance.get_attack_results_async(result_selection=AttackResultSelection.ALL_RESULTS) == []
