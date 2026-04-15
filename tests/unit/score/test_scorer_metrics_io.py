# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from pathlib import Path
from unittest.mock import patch

from pyrit.identifiers import ComponentIdentifier
from pyrit.score.scorer_evaluation.scorer_metrics import (
    HarmScorerMetrics,
    ObjectiveScorerMetrics,
    ScorerMetricsWithIdentity,
)
from pyrit.score.scorer_evaluation.scorer_metrics_io import (
    _append_jsonl_entry,
    _load_jsonl,
    _metrics_to_registry_dict,
    add_evaluation_results,
    find_harm_metrics_by_eval_hash,
    find_objective_metrics_by_eval_hash,
    get_all_harm_metrics,
    get_all_objective_metrics,
    replace_evaluation_results,
)


def _make_identifier(*, class_name: str = "TestScorer") -> ComponentIdentifier:
    return ComponentIdentifier(
        class_name=class_name,
        class_module="pyrit.score.test",
        params={"model_name": "gpt-4"},
    )


def _make_objective_metrics(**overrides) -> ObjectiveScorerMetrics:
    defaults = {
        "num_responses": 100,
        "num_human_raters": 3,
        "accuracy": 0.92,
        "accuracy_standard_error": 0.02,
        "f1_score": 0.91,
        "precision": 0.93,
        "recall": 0.90,
    }
    defaults.update(overrides)
    return ObjectiveScorerMetrics(**defaults)


def _make_harm_metrics(**overrides) -> HarmScorerMetrics:
    defaults = {
        "num_responses": 50,
        "num_human_raters": 2,
        "mean_absolute_error": 0.08,
        "mae_standard_error": 0.01,
        "t_statistic": 1.5,
        "p_value": 0.13,
        "krippendorff_alpha_combined": 0.85,
    }
    defaults.update(overrides)
    return HarmScorerMetrics(**defaults)


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


# --- _load_jsonl tests ---


def test_load_jsonl_file_not_found(tmp_path):
    result = _load_jsonl(tmp_path / "missing.jsonl")
    assert result == []


def test_load_jsonl_valid_entries(tmp_path):
    path = tmp_path / "data.jsonl"
    entries = [{"a": 1}, {"b": 2}]
    _write_jsonl(path, entries)
    result = _load_jsonl(path)
    assert result == entries


def test_load_jsonl_skips_invalid_json(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text('{"valid": true}\nnot json\n{"also_valid": true}\n', encoding="utf-8")
    result = _load_jsonl(path)
    assert len(result) == 2
    assert result[0] == {"valid": True}
    assert result[1] == {"also_valid": True}


def test_load_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text('{"a": 1}\n\n\n{"b": 2}\n', encoding="utf-8")
    result = _load_jsonl(path)
    assert len(result) == 2


# --- _append_jsonl_entry tests ---


def test_append_jsonl_entry_creates_file(tmp_path):
    import threading

    path = tmp_path / "subdir" / "out.jsonl"
    lock = threading.Lock()
    entry = {"key": "value"}
    _append_jsonl_entry(file_path=path, lock=lock, entry=entry)

    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0]) == entry


def test_append_jsonl_entry_appends(tmp_path):
    import threading

    path = tmp_path / "out.jsonl"
    _write_jsonl(path, [{"first": 1}])
    lock = threading.Lock()
    _append_jsonl_entry(file_path=path, lock=lock, entry={"second": 2})

    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2


# --- _metrics_to_registry_dict tests ---


def test_metrics_to_registry_dict_excludes_trial_scores():
    metrics = _make_objective_metrics()
    result = _metrics_to_registry_dict(metrics)
    assert "trial_scores" not in result


def test_metrics_to_registry_dict_excludes_none_values():
    metrics = _make_objective_metrics(average_score_time_seconds=None, dataset_name=None)
    result = _metrics_to_registry_dict(metrics)
    assert "average_score_time_seconds" not in result
    assert "dataset_name" not in result


def test_metrics_to_registry_dict_excludes_private_fields():
    metrics = _make_harm_metrics()
    result = _metrics_to_registry_dict(metrics)
    assert "_harm_definition_obj" not in result


def test_metrics_to_registry_dict_includes_values():
    metrics = _make_objective_metrics()
    result = _metrics_to_registry_dict(metrics)
    assert result["accuracy"] == 0.92
    assert result["f1_score"] == 0.91
    assert result["num_responses"] == 100


# --- find_objective_metrics_by_eval_hash tests ---


def test_find_objective_metrics_by_eval_hash_found(tmp_path):
    identifier = _make_identifier()
    entry = identifier.to_dict()
    entry["eval_hash"] = "hash_abc"
    entry["metrics"] = _metrics_to_registry_dict(_make_objective_metrics(accuracy=0.88))
    path = tmp_path / "objective_achieved_metrics.jsonl"
    _write_jsonl(path, [entry])

    result = find_objective_metrics_by_eval_hash(eval_hash="hash_abc", file_path=path)
    assert result is not None
    assert result.accuracy == 0.88


def test_find_objective_metrics_by_eval_hash_not_found(tmp_path):
    path = tmp_path / "objective_achieved_metrics.jsonl"
    _write_jsonl(path, [])
    result = find_objective_metrics_by_eval_hash(eval_hash="missing", file_path=path)
    assert result is None


def test_find_objective_metrics_by_eval_hash_missing_file(tmp_path):
    result = find_objective_metrics_by_eval_hash(eval_hash="nope", file_path=tmp_path / "nonexistent.jsonl")
    assert result is None


def test_find_objective_metrics_default_path():
    with patch("pyrit.score.scorer_evaluation.scorer_metrics_io._load_jsonl", return_value=[]) as mock_load:
        result = find_objective_metrics_by_eval_hash(eval_hash="test_hash")
        assert result is None
        call_args = mock_load.call_args[0][0]
        assert "objective" in str(call_args)
        assert "objective_achieved_metrics.jsonl" in str(call_args)


# --- find_harm_metrics_by_eval_hash tests ---


def test_find_harm_metrics_by_eval_hash_found():
    identifier = _make_identifier()
    entry = identifier.to_dict()
    entry["eval_hash"] = "harm_hash"
    entry["metrics"] = _metrics_to_registry_dict(_make_harm_metrics(mean_absolute_error=0.12))

    with patch("pyrit.score.scorer_evaluation.scorer_metrics_io._load_jsonl") as mock_load:
        mock_load.return_value = [entry]
        result = find_harm_metrics_by_eval_hash(eval_hash="harm_hash", harm_category="hate_speech")
    assert result is not None
    assert result.mean_absolute_error == 0.12


def test_find_harm_metrics_by_eval_hash_not_found():
    with patch("pyrit.score.scorer_evaluation.scorer_metrics_io._load_jsonl", return_value=[]):
        result = find_harm_metrics_by_eval_hash(eval_hash="missing", harm_category="violence")
    assert result is None


# --- get_all_objective_metrics tests ---


def test_get_all_objective_metrics_from_file(tmp_path):
    identifier = _make_identifier(class_name="Scorer1")
    metrics = _make_objective_metrics()
    entry = identifier.to_dict()
    entry["eval_hash"] = "h1"
    entry["metrics"] = _metrics_to_registry_dict(metrics)
    path = tmp_path / "objective_achieved_metrics.jsonl"
    _write_jsonl(path, [entry])

    results = get_all_objective_metrics(file_path=path)
    assert len(results) == 1
    assert isinstance(results[0], ScorerMetricsWithIdentity)
    assert results[0].metrics.accuracy == 0.92
    assert results[0].scorer_identifier.class_name == "Scorer1"


def test_get_all_objective_metrics_empty_file(tmp_path):
    path = tmp_path / "empty.jsonl"
    _write_jsonl(path, [])
    results = get_all_objective_metrics(file_path=path)
    assert results == []


def test_get_all_objective_metrics_default_path():
    with patch("pyrit.score.scorer_evaluation.scorer_metrics_io._load_metrics_from_file", return_value=[]) as mock_load:
        results = get_all_objective_metrics()
        assert results == []
        call_path = mock_load.call_args[1]["file_path"]
        assert "objective_achieved_metrics.jsonl" in str(call_path)


# --- get_all_harm_metrics tests ---


def test_get_all_harm_metrics():
    identifier = _make_identifier()
    metrics = _make_harm_metrics()
    entry = identifier.to_dict()
    entry["metrics"] = _metrics_to_registry_dict(metrics)

    with patch("pyrit.score.scorer_evaluation.scorer_metrics_io._load_jsonl") as mock_load:
        mock_load.return_value = [entry]
        results = get_all_harm_metrics(harm_category="hate_speech")
    assert len(results) == 1
    assert results[0].metrics.mean_absolute_error == 0.08


# --- add_evaluation_results tests ---


def test_add_evaluation_results_creates_entry(tmp_path):
    import pyrit.score.scorer_evaluation.scorer_metrics_io as sio

    original_locks = sio._file_write_locks.copy()
    try:
        path = tmp_path / "objective" / "test_metrics.jsonl"
        identifier = _make_identifier()
        metrics = _make_objective_metrics()

        add_evaluation_results(
            file_path=path,
            scorer_identifier=identifier,
            eval_hash="eval_abc",
            metrics=metrics,
        )

        assert path.exists()
        entries = _load_jsonl(path)
        assert len(entries) == 1
        assert entries[0]["eval_hash"] == "eval_abc"
        assert entries[0]["metrics"]["accuracy"] == 0.92
        assert entries[0]["class_name"] == "TestScorer"
    finally:
        sio._file_write_locks = original_locks


def test_add_evaluation_results_appends_multiple(tmp_path):
    import pyrit.score.scorer_evaluation.scorer_metrics_io as sio

    original_locks = sio._file_write_locks.copy()
    try:
        path = tmp_path / "test_metrics.jsonl"

        add_evaluation_results(
            file_path=path,
            scorer_identifier=_make_identifier(class_name="Scorer1"),
            eval_hash="h1",
            metrics=_make_objective_metrics(accuracy=0.80),
        )
        add_evaluation_results(
            file_path=path,
            scorer_identifier=_make_identifier(class_name="Scorer2"),
            eval_hash="h2",
            metrics=_make_objective_metrics(accuracy=0.90),
        )

        entries = _load_jsonl(path)
        assert len(entries) == 2
        assert entries[0]["eval_hash"] == "h1"
        assert entries[1]["eval_hash"] == "h2"
    finally:
        sio._file_write_locks = original_locks


# --- replace_evaluation_results tests ---


def test_replace_evaluation_results_replaces_existing(tmp_path):
    import pyrit.score.scorer_evaluation.scorer_metrics_io as sio

    original_locks = sio._file_write_locks.copy()
    try:
        path = tmp_path / "test_metrics.jsonl"
        identifier = _make_identifier()

        add_evaluation_results(
            file_path=path,
            scorer_identifier=identifier,
            eval_hash="h1",
            metrics=_make_objective_metrics(accuracy=0.80),
        )

        replace_evaluation_results(
            file_path=path,
            scorer_identifier=identifier,
            eval_hash="h1",
            metrics=_make_objective_metrics(accuracy=0.95),
        )

        entries = _load_jsonl(path)
        assert len(entries) == 1
        assert entries[0]["metrics"]["accuracy"] == 0.95
    finally:
        sio._file_write_locks = original_locks


def test_replace_evaluation_results_adds_when_not_exists(tmp_path):
    import pyrit.score.scorer_evaluation.scorer_metrics_io as sio

    original_locks = sio._file_write_locks.copy()
    try:
        path = tmp_path / "test_metrics.jsonl"

        replace_evaluation_results(
            file_path=path,
            scorer_identifier=_make_identifier(),
            eval_hash="new_hash",
            metrics=_make_objective_metrics(accuracy=0.85),
        )

        entries = _load_jsonl(path)
        assert len(entries) == 1
        assert entries[0]["eval_hash"] == "new_hash"
    finally:
        sio._file_write_locks = original_locks


def test_replace_evaluation_results_preserves_other_entries(tmp_path):
    import pyrit.score.scorer_evaluation.scorer_metrics_io as sio

    original_locks = sio._file_write_locks.copy()
    try:
        path = tmp_path / "test_metrics.jsonl"

        add_evaluation_results(
            file_path=path,
            scorer_identifier=_make_identifier(class_name="A"),
            eval_hash="keep_me",
            metrics=_make_objective_metrics(accuracy=0.70),
        )
        add_evaluation_results(
            file_path=path,
            scorer_identifier=_make_identifier(class_name="B"),
            eval_hash="replace_me",
            metrics=_make_objective_metrics(accuracy=0.80),
        )

        replace_evaluation_results(
            file_path=path,
            scorer_identifier=_make_identifier(class_name="B_new"),
            eval_hash="replace_me",
            metrics=_make_objective_metrics(accuracy=0.99),
        )

        entries = _load_jsonl(path)
        assert len(entries) == 2
        hashes = {e["eval_hash"] for e in entries}
        assert hashes == {"keep_me", "replace_me"}
        replaced = [e for e in entries if e["eval_hash"] == "replace_me"][0]
        assert replaced["metrics"]["accuracy"] == 0.99
    finally:
        sio._file_write_locks = original_locks
