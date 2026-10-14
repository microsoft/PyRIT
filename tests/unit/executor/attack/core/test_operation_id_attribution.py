# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch
from uuid import uuid4

import pytest
from unit.mocks import MockPromptTarget

from pyrit.executor.attack import PromptSendingAttack
from pyrit.executor.attack.compound import SequentialAttack, SequentialChildAttack
from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution
from pyrit.executor.attack.core.attack_strategy import AttackContext, AttackStrategy
from pyrit.memory import MemoryInterface
from pyrit.models import AttackResult, AttackResultSelection, AttackSeedGroup, Operation, SeedObjective


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
