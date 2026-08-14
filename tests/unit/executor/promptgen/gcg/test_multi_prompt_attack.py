# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

attack_manager_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.base.attack_manager",
    reason="GCG optional dependencies (torch, mlflow, etc.) not installed",
)
IndividualPromptAttack = attack_manager_mod.IndividualPromptAttack
MultiPromptAttack = attack_manager_mod.MultiPromptAttack
ProgressiveMultiPromptAttack = attack_manager_mod.ProgressiveMultiPromptAttack
EvaluateAttack = attack_manager_mod.EvaluateAttack


class _StubMultiPromptAttack:
    def run(self, **kwargs: Any) -> tuple[str, float, int]:
        return "updated control", 0.5, 1


class _RecordingMultiPromptAttackFactory:
    def __init__(self, *, logfile: Path) -> None:
        self._logfile = logfile
        self.params_at_creation: dict[str, Any] | None = None

    def __call__(self, *args: Any) -> _StubMultiPromptAttack:
        with self._logfile.open() as f:
            self.params_at_creation = json.load(f)["params"]
        return _StubMultiPromptAttack()


def _make_worker(*, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        model=SimpleNamespace(name_or_path=f"{name}-model"),
        tokenizer=SimpleNamespace(name_or_path=f"{name}-tokenizer", chat_template=f"{name}-template"),
    )


@pytest.mark.parametrize(
    ("attack_class", "additional_kwargs", "expected_param_keys"),
    [
        (
            IndividualPromptAttack,
            {},
            [
                "goals",
                "targets",
                "test_goals",
                "test_targets",
                "control_init",
                "test_prefixes",
                "models",
                "test_models",
            ],
        ),
        (
            ProgressiveMultiPromptAttack,
            {"progressive_goals": False, "progressive_models": True},
            [
                "goals",
                "targets",
                "test_goals",
                "test_targets",
                "progressive_goals",
                "progressive_models",
                "control_init",
                "test_prefixes",
                "models",
                "test_models",
            ],
        ),
        (
            EvaluateAttack,
            {},
            [
                "goals",
                "targets",
                "test_goals",
                "test_targets",
                "control_init",
                "test_prefixes",
                "models",
                "test_models",
            ],
        ),
    ],
)
def test_attack_manager_initializes_exact_log_schema(
    *,
    tmp_path: Path,
    attack_class: type[Any],
    additional_kwargs: dict[str, Any],
    expected_param_keys: list[str],
) -> None:
    logfile = tmp_path / "attack.json"

    attack_class(
        goals=["goal"],
        targets=["target"],
        workers=[_make_worker(name="train")],
        control_init="control",
        test_prefixes=["prefix"],
        logfile=str(logfile),
        managers={"MPA": _StubMultiPromptAttack},
        test_goals=["test goal"],
        test_targets=["test target"],
        test_workers=[_make_worker(name="test")],
        **additional_kwargs,
    )

    with logfile.open() as f:
        log = json.load(f)

    assert list(log) == ["params", "controls", "losses", "runtimes", "tests"]
    assert list(log["params"]) == expected_param_keys
    assert log["params"]["models"] == [
        {
            "model_path": "train-model",
            "tokenizer_path": "train-tokenizer",
            "chat_template": "train-template",
        }
    ]
    assert log["params"]["test_models"] == [
        {
            "model_path": "test-model",
            "tokenizer_path": "test-tokenizer",
            "chat_template": "test-template",
        }
    ]
    assert log["controls"] == []
    assert log["losses"] == []
    assert log["runtimes"] == []
    assert log["tests"] == []


@pytest.mark.parametrize(
    ("attack_class", "additional_kwargs"),
    [
        (IndividualPromptAttack, {}),
        (
            ProgressiveMultiPromptAttack,
            {"progressive_goals": False, "progressive_models": False},
        ),
    ],
)
def test_attack_manager_records_run_params_before_creating_mpa(
    *,
    tmp_path: Path,
    attack_class: type[Any],
    additional_kwargs: dict[str, Any],
) -> None:
    logfile = tmp_path / "attack.json"
    factory = _RecordingMultiPromptAttackFactory(logfile=logfile)
    attack = attack_class(
        goals=["goal"],
        targets=["target"],
        workers=[_make_worker(name="train")],
        logfile=str(logfile),
        managers={"MPA": factory},
        **additional_kwargs,
    )

    attack.run(
        n_steps=1,
        batch_size=2,
        topk=3,
        temp=0.5,
        allow_non_ascii=False,
        target_weight=0.75,
        control_weight=0.25,
        anneal=False,
        test_steps=4,
        incr_control=False,
        stop_on_success=False,
        verbose=False,
        filter_cand=False,
    )

    assert factory.params_at_creation is not None
    assert list(factory.params_at_creation)[-11:] == [
        "n_steps",
        "test_steps",
        "batch_size",
        "topk",
        "temp",
        "allow_non_ascii",
        "target_weight",
        "control_weight",
        "anneal",
        "incr_control",
        "stop_on_success",
    ]
    assert {key: factory.params_at_creation[key] for key in list(factory.params_at_creation)[-11:]} == {
        "n_steps": 1,
        "test_steps": 4,
        "batch_size": 2,
        "topk": 3,
        "temp": 0.5,
        "allow_non_ascii": False,
        "target_weight": 0.75,
        "control_weight": 0.25,
        "anneal": False,
        "incr_control": False,
        "stop_on_success": False,
    }


def test_evaluate_attack_records_test_count_and_empty_result_contract(tmp_path: Path) -> None:
    logfile = tmp_path / "attack.json"
    attack = EvaluateAttack(
        goals=["goal"],
        targets=["target"],
        workers=[_make_worker(name="train")],
        logfile=str(logfile),
        managers={"MPA": _StubMultiPromptAttack},
    )

    result = attack.run(steps=0, controls=[], batch_size=1, verbose=False)

    with logfile.open() as f:
        params = json.load(f)["params"]
    assert list(params)[-1] == "num_tests"
    assert params["num_tests"] == 0
    assert result == ([], [], [], [], [], [])


class TestFilterMpaKwargs:
    """Tests for the filter_mpa_kwargs static method."""

    def test_filters_mpa_prefixed_kwargs(self) -> None:
        """Should extract kwargs starting with 'mpa_' and strip the prefix."""
        result = ProgressiveMultiPromptAttack.filter_mpa_kwargs(
            mpa_batch_size=512,
            mpa_lr=0.01,
            other_param="ignored",
        )
        assert result == {"batch_size": 512, "lr": 0.01}

    def test_returns_empty_dict_when_no_mpa_kwargs(self) -> None:
        """Should return empty dict when no mpa_ prefixed kwargs are present."""
        result = ProgressiveMultiPromptAttack.filter_mpa_kwargs(
            batch_size=512,
            lr=0.01,
        )
        assert result == {}

    def test_individual_filter_matches_progressive(self) -> None:
        """IndividualPromptAttack.filter_mpa_kwargs should behave the same."""
        result = IndividualPromptAttack.filter_mpa_kwargs(
            mpa_n_steps=100,
            mpa_deterministic=True,
        )
        assert result == {"n_steps": 100, "deterministic": True}


class TestMultiPromptAttackParseResults:
    """Tests for MultiPromptAttack.parse_results method."""

    def _create_minimal_attack(
        self,
        *,
        n_train_workers: int,
        n_train_goals: int,
    ) -> MultiPromptAttack:
        """Create a MultiPromptAttack with minimal mock state for parse_results testing."""
        attack = object.__new__(MultiPromptAttack)
        # parse_results only uses len(self.workers) and len(self.goals)
        attack.workers = [None] * n_train_workers
        attack.goals = [""] * n_train_goals
        return attack

    def test_parse_results_basic(self) -> None:
        """Should correctly partition results into in-distribution/out-of-distribution quadrants."""
        attack = self._create_minimal_attack(n_train_workers=2, n_train_goals=2)

        # 4 workers (2 train + 2 test), 4 goals (2 train + 2 test)
        results = np.array(
            [
                [1, 0, 1, 1],  # train worker 1
                [0, 1, 0, 1],  # train worker 2
                [1, 1, 0, 0],  # test worker 1
                [0, 0, 1, 1],  # test worker 2
            ]
        )

        id_id, id_od, od_id, od_od = attack.parse_results(results)
        # id_id: train workers x train goals = results[:2, :2].sum() = 1+0+0+1 = 2
        assert id_id == 2
        # id_od: train workers x test goals = results[:2, 2:].sum() = 1+1+0+1 = 3
        assert id_od == 3
        # od_id: test workers x train goals = results[2:, :2].sum() = 1+1+0+0 = 2
        assert od_id == 2
        # od_od: test workers x test goals = results[2:, 2:].sum() = 0+0+1+1 = 2
        assert od_od == 2

    def test_parse_results_all_zeros(self) -> None:
        """Should handle all-zero results."""
        attack = self._create_minimal_attack(n_train_workers=1, n_train_goals=1)
        results = np.zeros((2, 2))

        id_id, id_od, od_id, od_od = attack.parse_results(results)
        assert id_id == 0
        assert id_od == 0
        assert od_id == 0
        assert od_od == 0
