# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from pathlib import Path

from unit.mocks import make_scenario_result

from build_scripts.export_adversarial_benchmark_result import _write_overview_async
from pyrit.models import AttackOutcome, AttackResult, ScenarioResult


def _attack_result() -> AttackResult:
    return AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective="test objective",
        outcome=AttackOutcome.SUCCESS,
    )


def _benchmark_result(
    *,
    attack_results: dict[str, list[AttackResult]],
    display_group_map: dict[str, str],
) -> ScenarioResult:
    return make_scenario_result(
        scenario_name="AdversarialBenchmark",
        objective_target_identifier=None,
        objective_scorer_identifier=None,
        attack_results=attack_results,
        display_group_map=display_group_map,
    )


async def test_write_overview_reports_three_techniques_two_models_six_combinations_async(tmp_path: Path) -> None:
    attack_results: dict[str, list[AttackResult]] = {}
    display_group_map: dict[str, str] = {}
    for technique in ("crescendo_simulated", "role_play_video_game", "tap"):
        for model in ("adversarial_model_a", "adversarial_model_b"):
            atomic_attack_name = f"{technique}__{model}_harmbench"
            attack_results[atomic_attack_name] = [_attack_result()]
            display_group_map[atomic_attack_name] = model
    result = _benchmark_result(attack_results=attack_results, display_group_map=display_group_map)

    await _write_overview_async(result=result, output_dir=tmp_path)

    overview = (tmp_path / "overview.txt").read_text(encoding="utf-8")
    assert "Distinct Techniques: 3" in overview
    assert "Distinct Adversarial Models: 2" in overview
    assert "Technique/Model Combinations: 6" in overview
    assert "Total Techniques:" not in overview
    assert "Total Attack Results: 6" in overview
    assert "Group: adversarial_model_a" in overview
    assert "Group: adversarial_model_b" in overview


async def test_write_overview_counts_planned_combinations_missing_from_loaded_attack_results_async(
    tmp_path: Path,
) -> None:
    display_group_map: dict[str, str] = {}
    for technique in ("crescendo_simulated", "role_play_video_game", "tap"):
        for model in ("adversarial_model_a", "adversarial_model_b"):
            display_group_map[f"{technique}__{model}_harmbench"] = model
    attack_results = {
        "tap__adversarial_model_a_harmbench": [_attack_result()],
        "tap__adversarial_model_b_harmbench": [_attack_result()],
    }
    result = _benchmark_result(attack_results=attack_results, display_group_map=display_group_map)

    await _write_overview_async(result=result, output_dir=tmp_path)

    overview = (tmp_path / "overview.txt").read_text(encoding="utf-8")
    assert "Distinct Techniques: 3" in overview
    assert "Distinct Adversarial Models: 2" in overview
    assert "Technique/Model Combinations: 6" in overview
    assert "Total Attack Results: 2" in overview


async def test_write_overview_deduplicates_datasets_without_attack_results_async(tmp_path: Path) -> None:
    display_group_map = {
        "tap__adversarial_model_a_harmbench": "adversarial_model_a",
        "tap__adversarial_model_a_advbench": "adversarial_model_a",
        "tap__adversarial_model_b_harmbench": "adversarial_model_b",
    }
    result = _benchmark_result(attack_results={}, display_group_map=display_group_map)

    await _write_overview_async(result=result, output_dir=tmp_path)

    overview = (tmp_path / "overview.txt").read_text(encoding="utf-8")
    assert "Distinct Techniques: 1" in overview
    assert "Distinct Adversarial Models: 2" in overview
    assert "Technique/Model Combinations: 2" in overview
    assert "Total Attack Results: 0" in overview
