# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import errno
import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit.models import ComponentIdentifier
from pyrit.score.scorer_evaluation.scorer_metrics import (
    HarmScorerMetrics,
    ObjectiveScorerMetrics,
    ScorerMetricsWithIdentity,
)
from pyrit.score.scorer_evaluation.scorer_metrics_io import (
    _append_jsonl_entry,
    _load_jsonl,
    _metrics_to_registry_dict,
    _rewrite_jsonl_atomically,
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
    entry = identifier.model_dump()
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
    entry = identifier.model_dump()
    entry["eval_hash"] = "harm_hash"
    entry["metrics"] = _metrics_to_registry_dict(_make_harm_metrics(mean_absolute_error=0.12))

    with patch("pyrit.score.scorer_evaluation.scorer_metrics_io._load_jsonl") as mock_load:
        mock_load.return_value = [entry]
        result = find_harm_metrics_by_eval_hash(eval_hash="harm_hash", harm_category="hate_speech")
    assert result is not None
    assert result.mean_absolute_error == 0.12


def test_find_harm_metrics_reads_entries_recorded_before_the_baseline_field():
    identifier = _make_identifier()
    entry = identifier.model_dump()
    entry["eval_hash"] = "harm_hash"
    metrics = _metrics_to_registry_dict(_make_harm_metrics(mean_absolute_error=0.12))
    metrics.pop("baseline_mean_absolute_error", None)
    entry["metrics"] = metrics

    with patch("pyrit.score.scorer_evaluation.scorer_metrics_io._load_jsonl") as mock_load:
        mock_load.return_value = [entry]
        result = find_harm_metrics_by_eval_hash(eval_hash="harm_hash", harm_category="hate_speech")
    assert result is not None
    assert result.baseline_mean_absolute_error is None


def test_harm_metrics_baseline_round_trips_through_json(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text(_make_harm_metrics(baseline_mean_absolute_error=0.29).to_json())

    loaded = HarmScorerMetrics.from_json_file(path)

    assert loaded.baseline_mean_absolute_error == 0.29


def test_find_harm_metrics_by_eval_hash_not_found():
    with patch("pyrit.score.scorer_evaluation.scorer_metrics_io._load_jsonl", return_value=[]):
        result = find_harm_metrics_by_eval_hash(eval_hash="missing", harm_category="violence")
    assert result is None


def test_find_harm_metrics_by_eval_hash_uses_explicit_file_path(tmp_path):
    path = tmp_path / "custom_metrics.jsonl"
    with patch("pyrit.score.scorer_evaluation.scorer_metrics_io._load_jsonl", return_value=[]) as mock_load:
        result = find_harm_metrics_by_eval_hash(eval_hash="missing", file_path=path)

    assert result is None
    assert mock_load.call_args[0][0] == path


@pytest.mark.parametrize(
    ("harm_category", "expected_file_name"),
    [
        ("REPRESENTATIONAL", "representational_metrics.jsonl"),
        ("SEXUAL_CONTENT", "sexual_metrics.jsonl"),
    ],
)
def test_find_harm_metrics_by_eval_hash_maps_canonical_category(harm_category, expected_file_name):
    with patch("pyrit.score.scorer_evaluation.scorer_metrics_io._load_jsonl", return_value=[]) as mock_load:
        result = find_harm_metrics_by_eval_hash(eval_hash="missing", harm_category=harm_category)

    assert result is None
    assert mock_load.call_args[0][0].name == expected_file_name


def test_find_harm_metrics_by_eval_hash_requires_path_or_category():
    with pytest.raises(ValueError, match="Either harm_category or file_path must be provided"):
        find_harm_metrics_by_eval_hash(eval_hash="missing")


# --- get_all_objective_metrics tests ---


def test_get_all_objective_metrics_from_file(tmp_path):
    identifier = _make_identifier(class_name="Scorer1")
    metrics = _make_objective_metrics()
    entry = identifier.model_dump()
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
    entry = identifier.model_dump()
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


def test_replace_evaluation_results_keeps_unparseable_lines(tmp_path):
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
        torn = '{"hash_b": "b", "metrics": {"acc'
        with open(path, "a", encoding="utf-8") as f:
            f.write(torn + "\n")

        replace_evaluation_results(
            file_path=path,
            scorer_identifier=_make_identifier(class_name="B_new"),
            eval_hash="new_hash",
            metrics=_make_objective_metrics(accuracy=0.99),
        )

        assert torn in path.read_text(encoding="utf-8"), "the rewrite deleted a line it could not read"
        hashes = {entry["eval_hash"] for entry in _load_jsonl(path)}
        assert hashes == {"keep_me", "new_hash"}
    finally:
        sio._file_write_locks = original_locks


def test_replace_evaluation_results_leaves_registry_intact_after_a_failed_read(tmp_path):
    import pyrit.score.scorer_evaluation.scorer_metrics_io as sio

    original_locks = sio._file_write_locks.copy()
    try:
        path = tmp_path / "test_metrics.jsonl"
        add_evaluation_results(
            file_path=path,
            scorer_identifier=_make_identifier(class_name="A"),
            eval_hash="hash_a",
            metrics=_make_objective_metrics(accuracy=0.70),
        )
        add_evaluation_results(
            file_path=path,
            scorer_identifier=_make_identifier(class_name="C"),
            eval_hash="hash_c",
            metrics=_make_objective_metrics(accuracy=0.80),
        )
        undecodable = path.read_bytes() + b'{"eval_hash": "bad", "metrics": \xff\xfe}\n'
        path.write_bytes(undecodable)

        with pytest.raises(UnicodeDecodeError):
            replace_evaluation_results(
                file_path=path,
                scorer_identifier=_make_identifier(class_name="New"),
                eval_hash="new_hash",
                metrics=_make_objective_metrics(accuracy=0.99),
            )

        assert path.read_bytes() == undecodable, "a partial read must not be rewritten over the registry"
    finally:
        sio._file_write_locks = original_locks


def test_replace_evaluation_results_preserves_raw_lines_and_endings(tmp_path):
    import pyrit.score.scorer_evaluation.scorer_metrics_io as sio

    original_locks = sio._file_write_locks.copy()
    try:
        path = tmp_path / "test_metrics.jsonl"
        original = (
            b'  {"eval_hash": "keep", "metrics": {"value": 1}}\r\n'
            b"\t  \r\n"
            b"\n"
            b"\tnot json  \n"
            b'  {"eval_hash": "other", "metrics": {"value": 2}}'
        )
        path.write_bytes(original)

        replace_evaluation_results(
            file_path=path,
            scorer_identifier=_make_identifier(),
            eval_hash="new_hash",
            metrics=_make_objective_metrics(accuracy=0.99),
        )

        rewritten = path.read_bytes()
        assert rewritten.startswith(original)
        assert rewritten.endswith(b"\n")
        assert [entry["eval_hash"] for entry in _load_jsonl(path)] == ["keep", "other", "new_hash"]
    finally:
        sio._file_write_locks = original_locks


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_replace_evaluation_results_preserves_existing_permissions(tmp_path):
    import pyrit.score.scorer_evaluation.scorer_metrics_io as sio

    original_locks = sio._file_write_locks.copy()
    try:
        path = tmp_path / "test_metrics.jsonl"
        path.write_text('{"eval_hash": "keep", "metrics": {}}\n', encoding="utf-8")
        path.chmod(0o664)

        replace_evaluation_results(
            file_path=path,
            scorer_identifier=_make_identifier(),
            eval_hash="new_hash",
            metrics=_make_objective_metrics(accuracy=0.99),
        )

        assert stat.S_IMODE(path.stat().st_mode) == 0o664
    finally:
        sio._file_write_locks = original_locks


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_existing_registry_permissions_are_applied_before_staging_write(tmp_path):
    import pyrit.score.scorer_evaluation.scorer_metrics_io as sio

    path = tmp_path / "test_metrics.jsonl"
    path.write_text('{"value": 1}\n', encoding="utf-8")
    path.chmod(0o600)

    real_create_staging_file = sio._create_staging_file
    real_fdopen = os.fdopen
    staging_paths = []

    def record_staging_file(file_path, mode=0o666):
        staging_path, fd = real_create_staging_file(file_path, mode=mode)
        staging_paths.append(staging_path)
        return staging_path, fd

    class PermissionCheckingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, content):
            assert stat.S_IMODE(staging_paths[0].stat().st_mode) == 0o600
            return self.stream.write(content)

    with (
        patch.object(sio, "_create_staging_file", side_effect=record_staging_file),
        patch.object(
            os,
            "fdopen",
            side_effect=lambda *args, **kwargs: PermissionCheckingWriter(real_fdopen(*args, **kwargs)),
        ),
    ):
        _rewrite_jsonl_atomically(path, ['{"value": 2}\n'])

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_existing_registry_staging_file_is_private_at_creation(tmp_path):
    path = tmp_path / "test_metrics.jsonl"
    path.write_text('{"value": 1}\n', encoding="utf-8")
    path.chmod(0o600)

    original_umask = os.umask(0o022)
    real_open = os.open
    creation_modes = []

    def record_creation_mode(file_path, flags, mode=0o777, **kwargs):
        fd = real_open(file_path, flags, mode, **kwargs)
        if Path(file_path).name.startswith(f"{path.name}.tmp-"):
            creation_modes.append(stat.S_IMODE(Path(file_path).stat().st_mode))
        return fd

    try:
        with patch.object(os, "open", side_effect=record_creation_mode):
            _rewrite_jsonl_atomically(path, ['{"value": 2}\n'])
    finally:
        os.umask(original_umask)

    assert creation_modes == [0o600]


@pytest.mark.skipif(os.name == "nt", reason="POSIX umask semantics")
def test_new_registry_uses_umask_permissions(tmp_path):
    path = tmp_path / "test_metrics.jsonl"
    original_umask = os.umask(0o022)
    try:
        _rewrite_jsonl_atomically(path, [json.dumps({"value": 1}) + "\n"])
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
    finally:
        os.umask(original_umask)


def test_cleanup_preserves_replace_error_and_removes_read_only_staging_file(tmp_path):
    path = tmp_path / "test_metrics.jsonl"
    original = b'{"value": 1}\n'
    path.write_bytes(original)
    real_unlink = Path.unlink
    cleanup_attempts: list[str] = []

    def fail_once_for_staging_file(self, missing_ok=False):
        if self.name.startswith(f"{path.name}.tmp-") and not cleanup_attempts:
            cleanup_attempts.append(self.name)
            raise PermissionError("read-only staging file")
        return real_unlink(self, missing_ok=missing_ok)

    with (
        patch(
            "pyrit.score.scorer_evaluation.scorer_metrics_io.os.replace",
            side_effect=PermissionError("replace failed"),
        ),
        patch.object(Path, "unlink", new=fail_once_for_staging_file),
    ):
        with pytest.raises(PermissionError, match="replace failed"):
            _rewrite_jsonl_atomically(path, [json.dumps({"value": 2}) + "\n"])

    assert cleanup_attempts
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(f"{path.name}.tmp-*"))


def test_rewrite_jsonl_atomically_uses_distinct_staging_files(tmp_path):
    path = tmp_path / "test_metrics.jsonl"
    names: list[str] = []
    real_replace = os.replace

    def record_replace(source, destination):
        names.append(Path(source).name)
        real_replace(source, destination)

    with patch("pyrit.score.scorer_evaluation.scorer_metrics_io.os.replace", side_effect=record_replace):
        _rewrite_jsonl_atomically(path, [json.dumps({"value": 1}) + "\n"])
        _rewrite_jsonl_atomically(path, [json.dumps({"value": 2}) + "\n"])

    assert len(names) == 2
    assert len(set(names)) == 2


def test_rewrite_jsonl_atomically_retries_staging_collisions(tmp_path: Path) -> None:
    path = tmp_path / "test_metrics.jsonl"
    path.write_bytes(b'{"value": 1}\n')
    collision = tmp_path / "test_metrics.jsonl.tmp-occupied"
    other_writer = b'{"value": "other writer"}\n'
    collision.write_bytes(other_writer)

    with patch(
        "pyrit.score.scorer_evaluation.scorer_metrics_io.secrets.token_hex",
        side_effect=["occupied", "available"],
    ) as token_hex:
        _rewrite_jsonl_atomically(path, ['{"value": 2}\n'])

    assert token_hex.call_count == 2
    assert path.read_bytes() == b'{"value": 2}\n'
    assert collision.read_bytes() == other_writer
    assert set(tmp_path.iterdir()) == {path, collision}


def test_rewrite_jsonl_atomically_preserves_files_when_staging_collisions_exhaust_retries(tmp_path: Path) -> None:
    path = tmp_path / "test_metrics.jsonl"
    original = b'{"value": 1}\n'
    path.write_bytes(original)
    collision = tmp_path / "test_metrics.jsonl.tmp-occupied"
    other_writer = b'{"value": "other writer"}\n'
    collision.write_bytes(other_writer)

    with patch(
        "pyrit.score.scorer_evaluation.scorer_metrics_io.secrets.token_hex", return_value="occupied"
    ) as token_hex:
        with pytest.raises(FileExistsError, match="Could not create a unique staging file"):
            _rewrite_jsonl_atomically(path, ['{"value": 2}\n'])

    assert token_hex.call_count == 100
    assert path.read_bytes() == original
    assert collision.read_bytes() == other_writer
    assert set(tmp_path.iterdir()) == {path, collision}


def test_rewrite_jsonl_atomically_closes_descriptor_after_fdopen_failure(tmp_path: Path) -> None:
    path = tmp_path / "test_metrics.jsonl"
    original = b'{"value": 1}\n'
    path.write_bytes(original)
    with patch.object(os, "fdopen", side_effect=OSError("fdopen failed")) as fdopen:
        with pytest.raises(OSError, match="fdopen failed"):
            _rewrite_jsonl_atomically(path, ['{"value": 2}\n'])

    with pytest.raises(OSError) as error:
        os.fstat(fdopen.call_args.args[0])
    assert error.value.errno == errno.EBADF
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(f"{path.name}.tmp-*"))


@pytest.mark.parametrize(
    "unlink_errors",
    [
        pytest.param([OSError("cleanup failed")], id="unlink"),
        pytest.param([PermissionError("read-only staging file"), OSError("cleanup failed")], id="read-only-retry"),
    ],
)
def test_cleanup_failure_is_logged_without_masking_replace_error(
    *, tmp_path: Path, caplog: pytest.LogCaptureFixture, unlink_errors: list[OSError]
) -> None:
    path = tmp_path / "test_metrics.jsonl"
    original = b'{"value": 1}\n'
    path.write_bytes(original)
    original_mode = stat.S_IMODE(path.stat().st_mode)
    replace_error = PermissionError("replace failed")

    with (
        patch.object(os, "replace", side_effect=replace_error),
        patch.object(Path, "unlink", autospec=True, side_effect=unlink_errors) as unlink,
    ):
        with pytest.raises(PermissionError, match="replace failed") as error:
            _rewrite_jsonl_atomically(path, ['{"value": 2}\n'])

    assert error.value is replace_error
    assert path.read_bytes() == original
    assert stat.S_IMODE(path.stat().st_mode) == original_mode
    staging_paths = list(tmp_path.glob(f"{path.name}.tmp-*"))
    assert len(staging_paths) == 1
    staging_path = staging_paths[0]
    assert [call.args[0] for call in unlink.call_args_list] == [staging_path] * len(unlink_errors)
    assert caplog.messages == [f"Failed to clean up staging file {staging_path}: cleanup failed"]
    staging_path.unlink()
