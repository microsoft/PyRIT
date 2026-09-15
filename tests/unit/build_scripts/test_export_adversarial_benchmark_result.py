# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from datetime import UTC, datetime
from pathlib import Path

from build_scripts.export_adversarial_benchmark_result import (
    _BENCHMARK_METRICS_KEY_FIELDS,
    _build_technique_metrics,
    _dataset_identity,
    _objective_identity,
    _upsert_benchmark_metrics,
)
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ScenarioIdentifier,
    ScenarioResult,
    ScorerIdentifier,
    TargetIdentifier,
)


def _target_identifier(*, model_name: str | None = None, underlying_model_name: str | None = None) -> TargetIdentifier:
    return TargetIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target.openai_chat_target",
        model_name=model_name,
        underlying_model_name=underlying_model_name,
    )


def _scorer_identifier(*, class_name: str = "TrueFalseCompositeScorer") -> ScorerIdentifier:
    return ScorerIdentifier(class_name=class_name, class_module="pyrit.score.true_false_composite_scorer")


def _attack_result(
    *,
    conversation_id: str = "conv-1",
    objective: str = "objective-1",
    outcome: AttackOutcome = AttackOutcome.SUCCESS,
    timestamp: datetime | None = None,
) -> AttackResult:
    return AttackResult(
        conversation_id=conversation_id,
        objective=objective,
        outcome=outcome,
        timestamp=timestamp or datetime.now(UTC),
    )


def _scenario_result(
    *,
    attack_results: dict[str, list[AttackResult]],
    display_group_map: dict[str, str] | None = None,
    objective_target: TargetIdentifier | None = None,
    objective_scorer: ScorerIdentifier | None = None,
    datasets: list[str] | None = None,
) -> ScenarioResult:
    scenario_identifier = ScenarioIdentifier(
        class_name="AdversarialBenchmark",
        class_module="pyrit.scenario.scenarios.benchmark.adversarial",
        objective_target=objective_target,
        objective_scorer=objective_scorer,
        datasets=datasets,
    )
    return ScenarioResult(
        scenario_identifier=scenario_identifier,
        attack_results=attack_results,
        display_group_map=display_group_map or {},
    )


def test_objective_identity_prefers_underlying_model_name() -> None:
    result = _scenario_result(
        attack_results={},
        objective_target=_target_identifier(model_name="gpt-4o-deployment", underlying_model_name="gpt-4o"),
        objective_scorer=_scorer_identifier(),
    )

    objective_target, objective_scorer = _objective_identity(result=result)

    assert objective_target == "gpt-4o"
    assert objective_scorer == "TrueFalseCompositeScorer"


def test_objective_identity_falls_back_to_model_name() -> None:
    result = _scenario_result(
        attack_results={},
        objective_target=_target_identifier(model_name="gpt-4o-deployment"),
        objective_scorer=_scorer_identifier(),
    )

    objective_target, _ = _objective_identity(result=result)

    assert objective_target == "gpt-4o-deployment"


def test_objective_identity_falls_back_to_class_name() -> None:
    result = _scenario_result(
        attack_results={},
        objective_target=_target_identifier(),
        objective_scorer=_scorer_identifier(),
    )

    objective_target, _ = _objective_identity(result=result)

    assert objective_target == "OpenAIChatTarget"


def test_objective_identity_unknown_when_identifiers_missing() -> None:
    result = _scenario_result(attack_results={})

    objective_target, objective_scorer = _objective_identity(result=result)

    assert objective_target == "<unknown>"
    assert objective_scorer == "<unknown>"


def test_dataset_identity_joins_sorted_datasets() -> None:
    result = _scenario_result(attack_results={}, datasets=["harmbench", "advbench"])

    assert _dataset_identity(result=result) == "advbench,harmbench"


def test_dataset_identity_unknown_when_missing() -> None:
    result = _scenario_result(attack_results={})

    assert _dataset_identity(result=result) == "<unknown>"


def test_build_technique_metrics_includes_identity_fields_on_every_row() -> None:
    result = _scenario_result(
        attack_results={
            "crescendo__variant_a": [_attack_result(outcome=AttackOutcome.SUCCESS)],
            "pair__variant_a": [_attack_result(outcome=AttackOutcome.FAILURE)],
        },
        display_group_map={"crescendo__variant_a": "gpt-4o", "pair__variant_a": "gpt-4o"},
        objective_target=_target_identifier(underlying_model_name="gpt-4o"),
        objective_scorer=_scorer_identifier(class_name="SelfAskRefusalScorer"),
        datasets=["harmbench"],
    )

    metrics = _build_technique_metrics(result=result)

    assert len(metrics) == 2
    for row in metrics:
        assert row["objective_target"] == "gpt-4o"
        assert row["objective_scorer"] == "SelfAskRefusalScorer"
        assert row["dataset"] == "harmbench"


def test_build_technique_metrics_unknown_identity_fallback() -> None:
    result = _scenario_result(
        attack_results={"crescendo__variant_a": [_attack_result()]},
        display_group_map={"crescendo__variant_a": "gpt-4o"},
    )

    metrics = _build_technique_metrics(result=result)

    assert metrics[0]["objective_target"] == "<unknown>"
    assert metrics[0]["objective_scorer"] == "<unknown>"
    assert metrics[0]["dataset"] == "<unknown>"


def _metrics_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "technique": "crescendo",
        "adversarial_model": "gpt-4o",
        "objective_target": "gpt-4o",
        "objective_scorer": "SelfAskRefusalScorer",
        "dataset": "harmbench",
        "total": 10,
        "success": 5,
        "failure": 5,
        "error": 0,
        "undetermined": 0,
        "retry_records": 0,
        "success_rate": 0.5,
    }
    row.update(overrides)
    return row


def test_upsert_benchmark_metrics_creates_new_store(tmp_path: Path) -> None:
    store_path = tmp_path / "nested" / "adversarial_benchmark_metrics.jsonl"
    metrics = [_metrics_row()]

    _upsert_benchmark_metrics(metrics=metrics, store_path=store_path)

    rows = [json.loads(line) for line in store_path.read_text(encoding="utf-8").splitlines()]
    assert rows == metrics


def test_upsert_benchmark_metrics_replaces_matching_key(tmp_path: Path) -> None:
    store_path = tmp_path / "adversarial_benchmark_metrics.jsonl"
    _upsert_benchmark_metrics(metrics=[_metrics_row(success=5, success_rate=0.5)], store_path=store_path)

    _upsert_benchmark_metrics(metrics=[_metrics_row(success=9, success_rate=0.9)], store_path=store_path)

    rows = [json.loads(line) for line in store_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["success_rate"] == 0.9


def test_upsert_benchmark_metrics_preserves_unrelated_rows(tmp_path: Path) -> None:
    store_path = tmp_path / "adversarial_benchmark_metrics.jsonl"
    other_technique = _metrics_row(technique="pair")
    _upsert_benchmark_metrics(metrics=[other_technique], store_path=store_path)

    _upsert_benchmark_metrics(metrics=[_metrics_row(technique="crescendo")], store_path=store_path)

    rows = [json.loads(line) for line in store_path.read_text(encoding="utf-8").splitlines()]
    techniques = {row["technique"] for row in rows}
    assert techniques == {"pair", "crescendo"}


def test_upsert_benchmark_metrics_key_fields_present_on_every_row() -> None:
    row = _metrics_row()

    assert all(field in row for field in _BENCHMARK_METRICS_KEY_FIELDS)
