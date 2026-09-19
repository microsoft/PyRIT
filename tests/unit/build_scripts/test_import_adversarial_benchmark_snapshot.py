# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from build_scripts.import_adversarial_benchmark_snapshot import (
    _fill_missing_identity,
    _load_metrics,
    import_benchmark_snapshot,
)

# --- _load_metrics ---


def test_load_metrics_returns_rows_from_a_valid_snapshot(tmp_path: Path) -> None:
    rows = [{"technique": "crescendo", "adversarial_model": "gpt-4o"}]
    snapshot_path = tmp_path / "technique-metrics.json"
    snapshot_path.write_text(json.dumps(rows), encoding="utf-8")

    assert _load_metrics(technique_metrics_json=snapshot_path) == rows


def test_load_metrics_rejects_non_list_json(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "technique-metrics.json"
    snapshot_path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a JSON list"):
        _load_metrics(technique_metrics_json=snapshot_path)


# --- _fill_missing_identity ---


def test_fill_missing_identity_backfills_absent_fields_with_supplied_defaults() -> None:
    rows = [{"technique": "tap", "adversarial_model": "gpt-4o"}]

    filled = _fill_missing_identity(
        metrics=rows,
        identity_defaults={
            "objective_target": "openai_chat",
            "objective_scorer": "SelfAskRefusalScorer",
            "dataset": "adversarial_benchmark_v1",
        },
    )

    assert filled == [
        {
            "technique": "tap",
            "adversarial_model": "gpt-4o",
            "objective_target": "openai_chat",
            "objective_scorer": "SelfAskRefusalScorer",
            "dataset": "adversarial_benchmark_v1",
        }
    ]


def test_fill_missing_identity_falls_back_to_unknown_without_a_default() -> None:
    rows = [{"technique": "tap", "adversarial_model": "gpt-4o"}]

    filled = _fill_missing_identity(
        metrics=rows,
        identity_defaults={"objective_target": "openai_chat", "objective_scorer": None, "dataset": None},
    )

    assert filled[0]["objective_target"] == "openai_chat"
    assert filled[0]["objective_scorer"] == "<unknown>"
    assert filled[0]["dataset"] == "<unknown>"


def test_fill_missing_identity_does_not_overwrite_existing_values() -> None:
    rows = [
        {
            "technique": "tap",
            "adversarial_model": "gpt-4o",
            "objective_target": "real_target",
            "objective_scorer": "real_scorer",
            "dataset": "real_dataset",
        }
    ]

    filled = _fill_missing_identity(
        metrics=rows,
        identity_defaults={
            "objective_target": "openai_chat",
            "objective_scorer": "SelfAskRefusalScorer",
            "dataset": "adversarial_benchmark_v1",
        },
    )

    assert filled[0]["objective_target"] == "real_target"
    assert filled[0]["objective_scorer"] == "real_scorer"
    assert filled[0]["dataset"] == "real_dataset"


def test_fill_missing_identity_does_not_mutate_input_rows() -> None:
    rows = [{"technique": "tap", "adversarial_model": "gpt-4o"}]

    _fill_missing_identity(metrics=rows, identity_defaults={"dataset": "adversarial_benchmark_v1"})

    assert "dataset" not in rows[0]


# --- import_benchmark_snapshot ---


def _write_snapshot(*, path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_import_benchmark_snapshot_upserts_rows_and_returns_count(tmp_path: Path) -> None:
    snapshot_path = _write_snapshot(
        path=tmp_path / "technique-metrics.json",
        rows=[
            {
                "technique": "crescendo",
                "adversarial_model": "gpt-4o",
                "objective_target": "gpt-4o",
                "objective_scorer": "SelfAskRefusalScorer",
                "dataset": "adversarial_benchmark_v1",
                "total": 24,
                "success": 12,
                "failure": 12,
                "error": 0,
                "undetermined": 0,
                "retry_records": 0,
                "success_rate": 0.5,
            }
        ],
    )
    store_path = tmp_path / "adversarial_benchmark_metrics.jsonl"

    count = import_benchmark_snapshot(technique_metrics_json=snapshot_path, benchmark_store_path=store_path)

    assert count == 1
    rows = [json.loads(line) for line in store_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["technique"] == "crescendo"
    assert rows[0]["success_rate"] == 0.5


def test_import_benchmark_snapshot_backfills_missing_identity_fields(tmp_path: Path) -> None:
    # A snapshot from an exporter version that predates objective_target/objective_scorer/dataset.
    snapshot_path = _write_snapshot(
        path=tmp_path / "technique-metrics.json",
        rows=[
            {
                "technique": "tap",
                "adversarial_model": "azure_openai_gpt4o",
                "total": 24,
                "success": 22,
                "failure": 2,
                "error": 0,
                "undetermined": 0,
                "retry_records": 0,
                "success_rate": 0.9167,
            }
        ],
    )
    store_path = tmp_path / "adversarial_benchmark_metrics.jsonl"

    count = import_benchmark_snapshot(
        technique_metrics_json=snapshot_path,
        benchmark_store_path=store_path,
        default_objective_target="openai_chat",
        default_dataset="adversarial_benchmark_v1",
    )

    assert count == 1
    rows = [json.loads(line) for line in store_path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["objective_target"] == "openai_chat"
    assert rows[0]["objective_scorer"] == "<unknown>"
    assert rows[0]["dataset"] == "adversarial_benchmark_v1"


def test_import_benchmark_snapshot_replaces_matching_key_row(tmp_path: Path) -> None:
    row = {
        "technique": "tap",
        "adversarial_model": "gpt-4o",
        "objective_target": "gpt-4o",
        "objective_scorer": "SelfAskRefusalScorer",
        "dataset": "adversarial_benchmark_v1",
        "total": 24,
        "success": 6,
        "failure": 18,
        "error": 0,
        "undetermined": 0,
        "retry_records": 0,
        "success_rate": 0.25,
    }
    store_path = tmp_path / "adversarial_benchmark_metrics.jsonl"
    import_benchmark_snapshot(
        technique_metrics_json=_write_snapshot(path=tmp_path / "first.json", rows=[row]),
        benchmark_store_path=store_path,
    )

    updated_row = {**row, "success": 18, "success_rate": 0.75}
    import_benchmark_snapshot(
        technique_metrics_json=_write_snapshot(path=tmp_path / "second.json", rows=[updated_row]),
        benchmark_store_path=store_path,
    )

    rows = [json.loads(line) for line in store_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["success_rate"] == 0.75


def test_import_benchmark_snapshot_uses_default_store_path_when_not_overridden(tmp_path: Path) -> None:
    default_path = tmp_path / "default_store.jsonl"
    snapshot_path = _write_snapshot(
        path=tmp_path / "technique-metrics.json",
        rows=[
            {
                "technique": "crescendo",
                "adversarial_model": "gpt-4o",
                "objective_target": "gpt-4o",
                "objective_scorer": "SelfAskRefusalScorer",
                "dataset": "harmbench",
            }
        ],
    )

    with patch(
        "build_scripts.import_adversarial_benchmark_snapshot.DEFAULT_BENCHMARK_STORE_PATH",
        default_path,
    ):
        import_benchmark_snapshot(technique_metrics_json=snapshot_path)

    assert default_path.exists()
