# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for typed optimization-iteration state in the GCG attack loop."""

import random
from typing import Any
from unittest.mock import MagicMock

import pytest

attack_manager_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.base.attack_manager",
    reason="attack_manager module not importable",
)
torch = pytest.importorskip("torch", reason="torch not installed")

MultiPromptAttack = attack_manager_mod.MultiPromptAttack
OptimizationRunState = attack_manager_mod.OptimizationRunState
ProgressiveMultiPromptAttack = attack_manager_mod.ProgressiveMultiPromptAttack
ProgressiveScheduleState = attack_manager_mod.ProgressiveScheduleState
StopReason = attack_manager_mod.StopReason


def _bare_multi_prompt_attack(step_results: list[tuple[str, float]]) -> MultiPromptAttack:
    """Build a MultiPromptAttack without __init__ whose step() replays canned results."""
    attack = object.__new__(MultiPromptAttack)
    prompt_manager = MagicMock()
    prompt_manager.control_str = "initial"
    attack.prompts = [prompt_manager]
    attack.workers = [MagicMock()]
    attack.control_str = "initial"
    attack.logfile = None
    attack.step = MagicMock(side_effect=list(step_results))
    return attack


class TestStopReason:
    def test_has_expected_members(self) -> None:
        assert StopReason.MAX_STEPS_REACHED == "max_steps_reached"
        assert StopReason.ALL_PROMPTS_JAILBROKEN == "all_prompts_jailbroken"


class TestOptimizationRunState:
    def test_counters_and_stop_reason_default(self) -> None:
        state = OptimizationRunState(control="c", best_control="c", loss=1e6, best_loss=1e6)

        assert state.steps_completed == 0
        assert state.runtime == 0.0
        assert state.stop_reason is None

    def test_candidate_loss_defaults_to_none(self) -> None:
        state = OptimizationRunState(control="c", best_control="c", loss=1e6, best_loss=1e6)

        assert state.candidate_loss is None


class TestProgressiveScheduleState:
    def test_defaults(self) -> None:
        schedule = ProgressiveScheduleState(goals_admitted=1, workers_admitted=2)

        assert schedule.steps_completed == 0
        assert schedule.loss == float("inf")
        assert schedule.stop_inner_on_success is False


class TestMultiPromptRunStateTracking:
    def test_run_sets_max_steps_reached_when_loop_exhausts(self) -> None:
        attack = _bare_multi_prompt_attack([("better", 1.0)])

        control, loss, steps = attack.run(n_steps=1, prev_loss=2.0, stop_on_success=False, anneal=True)

        assert (control, loss, steps) == ("better", 1.0, 1)
        state: OptimizationRunState | None = getattr(attack, "last_run_state", None)
        assert state is not None
        assert state.steps_completed == 1
        assert state.stop_reason == StopReason.MAX_STEPS_REACHED
        assert state.best_control == "better"
        assert state.best_loss == 1.0
        assert state.control == "better"

    def test_run_records_jailbroken_stop_reason_without_counting_final_check(self) -> None:
        attack = _bare_multi_prompt_attack([])
        attack.test = MagicMock(return_value=([[True]], [[1]], [[1.0]]))

        control, loss, steps = attack.run(n_steps=5, stop_on_success=True)

        # The unmeasured seed loss passes through honestly instead of a
        # sentinel; nothing stepped, so the incoming loss is still current.
        assert (control, loss, steps) == ("initial", float("inf"), 0)
        state: OptimizationRunState = attack.last_run_state
        assert state.steps_completed == 0
        assert state.stop_reason == StopReason.ALL_PROMPTS_JAILBROKEN
        attack.step.assert_not_called()

    def test_rejected_first_candidate_does_not_dethrone_seed(self) -> None:
        # With ``prev_loss=1.0`` and a rejected candidate at ``10.0``, the run
        # must keep reporting the starting suffix with its real loss; the
        # rejected candidate must not become the best result just because a
        # sentinel used to be larger.
        attack = _bare_multi_prompt_attack([("worse", 10.0)])

        control, loss, steps = attack.run(n_steps=1, prev_loss=1.0, stop_on_success=False, anneal=True)

        assert control == "initial"
        assert loss == 1.0
        assert steps == 1
        state: OptimizationRunState = attack.last_run_state
        assert state.control == "initial"
        assert state.loss == 1.0
        assert state.candidate_loss == 10.0
        assert state.best_control == "initial"
        assert state.best_loss == 1.0

    def test_rejected_candidate_keeps_active_suffix_and_loss(self) -> None:
        attack = _bare_multi_prompt_attack([("better", 1.0), ("worse", 5.0)])
        random.seed(2026)

        control, loss, steps = attack.run(n_steps=2, prev_loss=2.0, stop_on_success=False, anneal=True)

        # The worse candidate must be rejected by annealing with overwhelming
        # probability under this seed; the active suffix stays "better" and the
        # reported loss stays paired with it. The rejected candidate's loss is
        # still observable through ``candidate_loss``.
        assert control == "better"
        assert steps == 2
        state: OptimizationRunState = attack.last_run_state
        assert state.best_control == "better"
        assert state.best_loss == 1.0
        assert state.control == "better"
        assert state.loss == 1.0
        assert state.candidate_loss == 5.0
        assert state.stop_reason == StopReason.MAX_STEPS_REACHED

    def test_failed_run_clears_stale_last_run_state(self) -> None:
        attack = _bare_multi_prompt_attack([("better", 1.0)])
        stale = OptimizationRunState(control="stale", best_control="stale", loss=0.1, best_loss=0.1)
        attack.last_run_state = stale
        attack.step = MagicMock(side_effect=RuntimeError("model exploded"))

        with pytest.raises(RuntimeError, match="model exploded"):
            attack.run(n_steps=3, stop_on_success=False)

        # A run that raises mid-loop must not leave the previous run's state
        # looking current.
        assert attack.last_run_state is None

    def test_periodic_checkpoint_restores_active_suffix(self) -> None:
        attack = _bare_multi_prompt_attack([("better", 1.0), ("best-yet", 0.25)])
        attack.logfile = "unused-by-test.json"  # gate for periodic checkpoints; log/test_all are mocked
        attack.test_all = MagicMock(return_value=([[False]], [[0]], [[0.5]]))
        attack.log = MagicMock()

        attack.run(
            n_steps=2,
            prev_loss=2.0,
            stop_on_success=False,
            anneal=True,
            test_steps=1,
        )

        # Each periodic checkpoint evaluates the best-known suffix and then
        # restores whatever suffix was active for optimization.
        assert attack.control_str == "best-yet"
        assert attack.log.call_count == 2
        first_log_args = attack.log.call_args_list[0].args
        assert first_log_args[2] == "better"
        second_log_args = attack.log.call_args_list[1].args
        assert second_log_args[2] == "best-yet"

    def test_seeded_runs_produce_identical_trajectories(self) -> None:
        results = []
        for _ in range(2):
            random.seed(1234)
            attack = _bare_multi_prompt_attack([("a", 3.0), ("b", 2.0), ("c", 1.5)])
            results.append(attack.run(n_steps=3, prev_loss=4.0, stop_on_success=False, anneal=True))

        assert results[0] == results[1]
        assert results[0] == ("c", 1.5, 3)


class TestGCGCandidateSelection:
    def test_selects_minimum_within_single_group(self) -> None:
        from pyrit.executor.promptgen.gcg.attack.gcg.gcg_attack import GCGMultiPromptAttack

        attack = object.__new__(GCGMultiPromptAttack)
        next_control, cand_loss = attack._select_best_candidate(
            control_cands=[["aa", "bb"]],
            losses=torch.tensor([0.5, 9.0]),
            batch_size=2,
        )

        assert next_control == "aa"
        assert cand_loss.item() == pytest.approx(0.5)

    def test_decomposes_cross_group_argmin_index(self) -> None:
        from pyrit.executor.promptgen.gcg.attack.gcg.gcg_attack import GCGMultiPromptAttack

        attack = object.__new__(GCGMultiPromptAttack)
        next_control, cand_loss = attack._select_best_candidate(
            control_cands=[["aa", "bb"], ["cc", "dd"]],
            losses=torch.tensor([9.0, 8.0, 7.0, 6.0]),
            batch_size=2,
        )

        assert next_control == "dd"
        assert cand_loss.item() == pytest.approx(6.0)


class TestProgressiveRunScheduleState:
    def _bare_progressive_attack(self, inner_attack: Any) -> ProgressiveMultiPromptAttack:
        progressive = object.__new__(ProgressiveMultiPromptAttack)
        progressive.goals = ["goal"]
        progressive.targets = ["target"]
        progressive.workers = [MagicMock()]
        progressive.test_goals = []
        progressive.test_targets = []
        progressive.test_workers = []
        progressive.test_prefixes = []
        progressive.managers = {"MPA": MagicMock(return_value=inner_attack)}
        progressive.control = "initial"
        progressive.logfile = None
        progressive.progressive_goals = True
        progressive.progressive_models = True
        return progressive

    def test_finalize_phase_logs_final_evaluation_and_stops(self) -> None:
        inner_attack = MagicMock()
        inner_attack.run.return_value = ("ctrl", 0.5, 2)
        model_tests = ([[True]], [[1]], [[1.0]])
        inner_attack.test_all.return_value = model_tests
        progressive = self._bare_progressive_attack(inner_attack)

        control, steps = progressive.run(n_steps=10, stop_on_success=True)

        assert (control, steps) == ("ctrl", 2)
        schedule: ProgressiveScheduleState = progressive.last_schedule_state
        assert schedule.steps_completed == 2
        assert schedule.goals_admitted == 1
        assert schedule.workers_admitted == 1
        inner_attack.test_all.assert_called_once()
        inner_attack.log.assert_called_once_with(2, 10, "ctrl", 0.5, 0.0, model_tests, verbose=True)

    def test_schedule_exhaustion_continues_until_step_budget_spent(self) -> None:
        inner_attack = MagicMock()
        inner_attack.run.return_value = ("ctrl", 0.5, 2)
        progressive = self._bare_progressive_attack(inner_attack)

        control, steps = progressive.run(n_steps=10, stop_on_success=False)

        assert (control, steps) == ("ctrl", 10)
        schedule: ProgressiveScheduleState = progressive.last_schedule_state
        assert schedule.steps_completed == 10
        assert schedule.stop_inner_on_success is False
        inner_attack.run.assert_called_with(
            n_steps=2,
            batch_size=1024,
            topk=256,
            temp=1.0,
            allow_non_ascii=False,
            target_weight=None,
            control_weight=None,
            anneal=True,
            anneal_from=8,
            # The inner result's loss feeds back as the next phase's prev_loss
            # so the annealing temperature schedule stays continuous across
            # progressive admissions.
            prev_loss=0.5,
            stop_on_success=False,
            test_steps=50,
            filter_cand=True,
            verbose=True,
        )

    def test_schedule_loss_carried_on_schedule_object(self) -> None:
        # The loss fed back between progressive rounds lives on the schedule
        # state (not a loose local), so ``last_schedule_state.loss`` reflects
        # the final inner run instead of staying at its ``inf`` default.
        inner_attack = MagicMock()
        inner_attack.run.return_value = ("ctrl", 0.75, 3)
        progressive = self._bare_progressive_attack(inner_attack)

        progressive.run(n_steps=6, stop_on_success=False)

        schedule: ProgressiveScheduleState = progressive.last_schedule_state
        assert schedule.steps_completed == 6
        assert schedule.loss == 0.75

    def test_failed_rerun_clears_stale_schedule_state_before_setup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inner_attack = MagicMock()
        inner_attack.run.return_value = ("ctrl", 0.5, 2)
        progressive = self._bare_progressive_attack(inner_attack)

        progressive.run(n_steps=2, stop_on_success=False)
        assert progressive.last_schedule_state is not None

        # A rerun that fails during fallible setup (logfile handling happens
        # before anything else) must not leave the previous run's schedule
        # state looking current.
        def _explode(logfile: Any, params: dict[str, Any]) -> None:
            raise RuntimeError("corrupt logfile")

        monkeypatch.setattr(attack_manager_mod, "_update_attack_log_params", _explode)

        with pytest.raises(RuntimeError, match="corrupt logfile"):
            progressive.run(n_steps=2, stop_on_success=False)

        assert progressive.last_schedule_state is None

    def test_exact_budget_goal_transition_does_not_reset_loss(self) -> None:
        # Two progressive goals; the inner run spends the whole remaining
        # budget exactly when the second goal would be admitted. The run must
        # return instead of stranding an ``inf`` on the carried loss.
        inner_attack = MagicMock()
        inner_attack.run.return_value = ("ctrl", 0.75, 3)
        progressive = self._bare_progressive_attack(inner_attack)
        progressive.goals = ["goal-1", "goal-2"]
        progressive.targets = ["target-1", "target-2"]

        control, steps = progressive.run(n_steps=3, stop_on_success=False)

        assert (control, steps) == ("ctrl", 3)
        schedule: ProgressiveScheduleState = progressive.last_schedule_state
        assert schedule.loss == 0.75
        assert schedule.goals_admitted == 1

    def test_exact_budget_worker_transition_does_not_reset_loss(self) -> None:
        # Same as the goal case, but with goals admitted up front and a second
        # worker waiting: an exact-budget exhaustion must skip the admission
        # and its sentinel reset.
        inner_attack = MagicMock()
        inner_attack.run.return_value = ("ctrl", 0.6, 3)
        progressive = self._bare_progressive_attack(inner_attack)
        progressive.progressive_goals = False
        progressive.workers = [MagicMock(), MagicMock()]

        control, steps = progressive.run(n_steps=3, stop_on_success=False)

        assert (control, steps) == ("ctrl", 3)
        schedule: ProgressiveScheduleState = progressive.last_schedule_state
        assert schedule.loss == 0.6
        assert schedule.workers_admitted == 1

    def test_exact_budget_control_weight_increase_is_skipped(self) -> None:
        # A fully-admitted schedule that exhausts its budget must not bump the
        # control weight (and reset the loss) for a round that never runs.
        inner_attack = MagicMock()
        inner_attack.run.return_value = ("ctrl", 0.8, 3)
        progressive = self._bare_progressive_attack(inner_attack)
        progressive.progressive_goals = False
        progressive.progressive_models = False

        control, steps = progressive.run(n_steps=3, control_weight=0.05, stop_on_success=False)

        assert (control, steps) == ("ctrl", 3)
        schedule: ProgressiveScheduleState = progressive.last_schedule_state
        assert schedule.loss == 0.8
        inner_attack.run.assert_called_once()

    def test_control_weight_increase_still_applies_while_budget_remains(self) -> None:
        # Guard against over-gating: with budget left after an inner round,
        # the weight ratchet must still fire and fund the next round.
        inner_attack = MagicMock()
        inner_attack.run.return_value = ("ctrl", 0.8, 2)
        progressive = self._bare_progressive_attack(inner_attack)
        progressive.progressive_goals = False
        progressive.progressive_models = False

        control, steps = progressive.run(n_steps=4, control_weight=0.05, stop_on_success=False)

        assert (control, steps) == ("ctrl", 4)
        second_call_kwargs = inner_attack.run.call_args_list[1].kwargs
        assert second_call_kwargs["control_weight"] == pytest.approx(0.06)

    def test_non_finite_inner_loss_completes_without_assertion(self) -> None:
        # A completed inner run may legitimately report a non-finite loss
        # (non-finite model loss or numeric overflow). The post-run invariant
        # must track that the loss was measured rather than infer update state
        # from the numeric value, so this run must complete normally.
        inner_attack = MagicMock()
        inner_attack.run.return_value = ("ctrl", float("inf"), 2)
        progressive = self._bare_progressive_attack(inner_attack)

        control, steps = progressive.run(n_steps=2, stop_on_success=False)

        assert (control, steps) == ("ctrl", 2)
        schedule: ProgressiveScheduleState = progressive.last_schedule_state
        assert schedule.steps_completed == 2
        assert schedule.loss == float("inf")
