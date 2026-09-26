# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Utility functions for reading/writing scorer evaluation metrics to JSONL files.
Thread-safe operations for appending entries.
"""

import json
import logging
import os
import secrets
import stat
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypeVar

from pyrit.common.path import (
    SCORER_EVALS_PATH,
)
from pyrit.models import ComponentIdentifier
from pyrit.models.harm_category import HarmCategory
from pyrit.score.scorer_evaluation.scorer_metrics import (
    HarmScorerMetrics,
    ObjectiveScorerMetrics,
    ScorerMetrics,
    ScorerMetricsWithIdentity,
    non_finite_to_none,
)

logger = logging.getLogger(__name__)

# Thread locks for writing (module-level, persists for application lifetime)
# Locks are created per file path to ensure thread-safe writes
_file_write_locks: dict[str, threading.Lock] = {}

M = TypeVar("M", bound=ScorerMetrics)

_HARM_METRICS_FILES_BY_CATEGORY = {
    HarmCategory.REPRESENTATIONAL.name: "representational_metrics.jsonl",
    HarmCategory.SEXUAL_CONTENT.name: "sexual_metrics.jsonl",
}


def _metrics_to_registry_dict(metrics: ScorerMetrics) -> dict[str, Any]:
    """
    Convert metrics to a dictionary suitable for registry storage.

    Excludes:
    - trial_scores (too large for registry storage)
    - Internal fields starting with '_'

    A statistic that is not a number (``NaN``) is kept, written as ``None`` so it serializes to
    JSON ``null``: it carries the information that the value is undefined, which dropping the key
    would lose, and the loading side declares those fields ``float | None``.

    Args:
        metrics (ScorerMetrics): The metrics object to convert.

    Returns:
        Dict: A dictionary with excluded fields removed.
    """
    metrics_dict = asdict(metrics)
    excluded_keys = {"trial_scores"}
    kept = {k: v for k, v in metrics_dict.items() if k not in excluded_keys and v is not None and not k.startswith("_")}
    return non_finite_to_none(kept)


def get_all_objective_metrics(
    file_path: Path | None = None,
) -> list[ScorerMetricsWithIdentity[ObjectiveScorerMetrics]]:
    """
    Load all objective scorer metrics with full scorer identity for comparison.

    Returns a list of ScorerMetricsWithIdentity[ObjectiveScorerMetrics] objects that wrap
    the scorer's identity information and its performance metrics, enabling clean attribute
    access like `entry.metrics.accuracy` or `entry.metrics.f1_score`.

    Args:
        file_path (Path | None): Path to a specific JSONL file to load.
            If not provided, uses the default path:
            SCORER_EVALS_PATH / "objective" / "objective_achieved_metrics.jsonl"

    Returns:
        list[ScorerMetricsWithIdentity[ObjectiveScorerMetrics]]: List of metrics with scorer identity.
            Access metrics via `entry.metrics.accuracy`, `entry.metrics.f1_score`, etc.
            Access scorer info via `entry.scorer_identifier.class_name`, etc.
    """
    if file_path is None:
        file_path = SCORER_EVALS_PATH / "objective" / "objective_achieved_metrics.jsonl"

    return _load_metrics_from_file(file_path=file_path, metrics_class=ObjectiveScorerMetrics)


def get_all_harm_metrics(
    harm_category: str,
) -> list[ScorerMetricsWithIdentity[HarmScorerMetrics]]:
    """
    Load all harm scorer metrics for a specific harm category.

    Returns a list of ScorerMetricsWithIdentity[HarmScorerMetrics] objects that wrap
    the scorer's identity information and its performance metrics, enabling clean attribute
    access like `entry.metrics.mean_absolute_error` or `entry.metrics.harm_category`.

    Args:
        harm_category (str): The harm category to load metrics for (e.g., "hate_speech", "violence").

    Returns:
        list[ScorerMetricsWithIdentity[HarmScorerMetrics]]: List of metrics with scorer identity.
            Access metrics via `entry.metrics.mean_absolute_error`, `entry.metrics.harm_category`, etc.
            Access scorer info via `entry.scorer_identifier.class_name`, etc.
    """
    file_path = SCORER_EVALS_PATH / "harm" / f"{harm_category}_metrics.jsonl"
    return _load_metrics_from_file(file_path=file_path, metrics_class=HarmScorerMetrics)


def _load_metrics_from_file(
    *,
    file_path: Path,
    metrics_class: type[M],
) -> list[ScorerMetricsWithIdentity[M]]:
    """
    Load scorer metrics from a JSONL file with the specified metrics class.

    This is a private helper function used by get_all_objective_metrics and get_all_harm_metrics.

    Args:
        file_path (Path): Path to the JSONL file to load.
        metrics_class (type[M]): The metrics class to instantiate (ObjectiveScorerMetrics or HarmScorerMetrics).

    Returns:
        list[ScorerMetricsWithIdentity[M]]: List of metrics with scorer identity.
    """
    results: list[ScorerMetricsWithIdentity[M]] = []
    entries = _load_jsonl(file_path)

    for entry in entries:
        metrics_dict = entry.get("metrics", {})
        # Filter out internal fields that have init=False (e.g., _harm_definition_obj)
        metrics_dict = {k: v for k, v in metrics_dict.items() if not k.startswith("_")}

        # Extract scorer identity (everything except metrics)
        identity_dict = {k: v for k, v in entry.items() if k not in ("metrics", "eval_hash")}

        try:
            # Reconstruct ComponentIdentifier from the stored dict
            scorer_identifier = ComponentIdentifier.model_validate(identity_dict)

            # Create the metrics object
            metrics = metrics_class(**metrics_dict)

            results.append(
                ScorerMetricsWithIdentity(
                    scorer_identifier=scorer_identifier,
                    metrics=metrics,
                )
            )
        except Exception as e:
            logger.warning(f"Failed to parse metrics entry: {e}")
            continue

    return results


def find_objective_metrics_by_eval_hash(
    *,
    eval_hash: str,
    file_path: Path | None = None,
) -> ObjectiveScorerMetrics | None:
    """
    Find objective scorer metrics by evaluation hash.

    Args:
        eval_hash (str): The scorer evaluation hash to search for.
        file_path (Path | None): Path to the JSONL file to search.
            If not provided, uses the default path:
            SCORER_EVALS_PATH / "objective" / "objective_achieved_metrics.jsonl"

    Returns:
        ObjectiveScorerMetrics if found, else None.
    """
    if file_path is None:
        file_path = SCORER_EVALS_PATH / "objective" / "objective_achieved_metrics.jsonl"

    return _find_metrics_by_eval_hash(file_path=file_path, eval_hash=eval_hash, metrics_class=ObjectiveScorerMetrics)


def find_harm_metrics_by_eval_hash(
    *,
    eval_hash: str,
    harm_category: str | None = None,
    file_path: Path | None = None,
) -> HarmScorerMetrics | None:
    """
    Find harm scorer metrics by evaluation hash.

    Args:
        eval_hash (str): The scorer evaluation hash to search for.
        harm_category (str | None): The harm category to search in (e.g., "hate_speech", "violence").
            Used to resolve the default registry path when file_path is not provided.
        file_path (Path | None): Path to a specific JSONL file to search.

    Returns:
        HarmScorerMetrics if found, else None.

    Raises:
        ValueError: If neither harm_category nor file_path is provided.
    """
    if file_path is None:
        if harm_category is None:
            raise ValueError("Either harm_category or file_path must be provided.")
        file_name = _HARM_METRICS_FILES_BY_CATEGORY.get(harm_category, f"{harm_category}_metrics.jsonl")
        file_path = SCORER_EVALS_PATH / "harm" / file_name
    return _find_metrics_by_eval_hash(file_path=file_path, eval_hash=eval_hash, metrics_class=HarmScorerMetrics)


def _find_metrics_by_eval_hash(
    *,
    file_path: Path,
    eval_hash: str,
    metrics_class: type[M],
) -> M | None:
    """
    Find scorer metrics by evaluation hash in a specific file.

    This is a private helper function used by find_objective_metrics_by_eval_hash
    and find_harm_metrics_by_eval_hash.

    Args:
        file_path (Path): Path to the JSONL file to search.
        eval_hash (str): The scorer evaluation hash to search for.
        metrics_class (type[M]): The metrics class to instantiate.

    Returns:
        The metrics instance if found, else None.
    """
    entries = _load_jsonl(file_path)

    for entry in entries:
        if entry.get("eval_hash") == eval_hash:
            metrics_dict = entry.get("metrics", {})
            # Filter out internal fields that have init=False (e.g., _harm_definition_obj)
            metrics_dict = {k: v for k, v in metrics_dict.items() if not k.startswith("_")}
            try:
                return metrics_class(**metrics_dict)
            except Exception as e:
                logger.warning(f"Failed to parse metrics for eval_hash {eval_hash}: {e}")
                return None

    return None


def add_evaluation_results(
    *,
    file_path: Path,
    scorer_identifier: ComponentIdentifier,
    eval_hash: str,
    metrics: "ScorerMetrics",
) -> None:
    """
    Append scorer metrics entry to the specified evaluation results file (thread-safe).

    This unified function handles both objective and harm scorer metrics, writing to
    the specified file path with appropriate validation and thread safety.

    Args:
        file_path (Path): The full path to the JSONL file to append to.
        scorer_identifier (ComponentIdentifier): The scorer's configuration identifier.
        eval_hash (str): The pre-computed evaluation hash for grouping.
        metrics (ScorerMetrics): The computed metrics (ObjectiveScorerMetrics or HarmScorerMetrics).
    """
    # Get or create lock for this file path
    file_path_str = str(file_path)
    if file_path_str not in _file_write_locks:
        _file_write_locks[file_path_str] = threading.Lock()

    # Build entry dictionary
    entry = scorer_identifier.model_dump()
    entry["eval_hash"] = eval_hash
    entry["metrics"] = _metrics_to_registry_dict(metrics)

    # Write to file with thread safety
    _append_jsonl_entry(
        file_path=file_path,
        lock=_file_write_locks[file_path_str],
        entry=entry,
    )

    logger.info(f"Added metrics for {scorer_identifier.class_name} to {file_path.name}")


def _load_jsonl(file_path: Path) -> list[dict[str, Any]]:
    """
    Load entries from a JSONL file.

    Args:
        file_path: Path to the JSONL file.

    Returns:
        List of dictionaries, one per line.
    """
    if not file_path.exists():
        logger.debug(f"Registry file not found: {file_path}")
        return []

    entries = []
    try:
        with open(file_path, encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON at line {line_num} in {file_path}: {e}")
    except Exception as e:
        logger.error(f"Failed to load registry from {file_path}: {e}")

    return entries


def _append_jsonl_entry(file_path: Path, lock: threading.Lock, entry: dict[str, Any]) -> None:
    """
    Append an entry to a JSONL file with thread safety.

    Args:
        file_path: Path to the JSONL file.
        lock: Threading lock to ensure atomic writes.
        entry: Dictionary to append as JSON line.
    """
    with lock:
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "a", encoding="utf-8") as f:
                # allow_nan=False: the registry is checked-in data that other tools read, and the
                # bare NaN token json.dumps would emit for it is not JSON.
                f.write(json.dumps(entry, allow_nan=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to write to registry {file_path}: {e}")
            raise


def _read_registry_lines(file_path: Path) -> list[tuple[str, dict[str, Any] | None]]:
    """
    Load a registry as (raw line, parsed entry) pairs, keeping unparseable lines.

    Unlike ``_load_jsonl``, which is a lookup helper that may ignore what it cannot
    read, this is the source of truth for a rewrite: every line in the file has to be
    accounted for, so a line that is not valid JSON is returned with ``None`` and the
    caller decides what to do with it. Read errors propagate instead of yielding a
    short list, because a rewrite built from a partial read would delete the rest of
    the registry.

    Args:
        file_path (Path): Path to the JSONL file.

    Returns:
        list[tuple[str, dict[str, Any] | None]]: One pair per line, with the raw line
            (including its original whitespace and line ending) and the parsed entry
            (or ``None`` when the line is not a JSON object). Empty when the file
            does not exist.

    Raises:
        OSError: If the file exists but cannot be read.
        UnicodeDecodeError: If the file is not valid UTF-8.
    """
    if not file_path.exists():
        logger.debug(f"Registry file not found: {file_path}")
        return []

    lines: list[tuple[str, dict[str, Any] | None]] = []
    with open(file_path, encoding="utf-8", newline="") as f:
        for line_num, raw_line in enumerate(f, 1):
            stripped = raw_line.strip()
            if not stripped:
                lines.append((raw_line, None))
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON at line {line_num} in {file_path}: {e}")
                lines.append((raw_line, None))
                continue
            lines.append((raw_line, parsed if isinstance(parsed, dict) else None))
    return lines


def _create_staging_file(file_path: Path, mode: int = 0o666) -> tuple[Path, int]:
    """
    Create an exclusive staging file whose requested mode is filtered by the process umask.

    Returns:
        tuple[Path, int]: The staging path and its open file descriptor.

    Raises:
        FileExistsError: If no unique staging name could be created.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(100):
        temp_path = file_path.with_name(f"{file_path.name}.tmp-{secrets.token_hex(8)}")
        try:
            return temp_path, os.open(temp_path, flags, mode)
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not create a unique staging file next to {file_path}")


def _cleanup_staging_file(temp_path: Path) -> None:
    """Remove a staging file without masking an earlier write/replace error."""
    try:
        temp_path.unlink(missing_ok=True)
    except PermissionError:
        try:
            # Windows refuses to unlink a read-only file. Make only this disposable
            # staging file writable; the registry's permissions remain untouched.
            temp_path.chmod(stat.S_IREAD | stat.S_IWRITE)
            temp_path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            logger.warning("Failed to clean up staging file %s: %s", temp_path, cleanup_error)
    except OSError as cleanup_error:
        logger.warning("Failed to clean up staging file %s: %s", temp_path, cleanup_error)


def _rewrite_jsonl_atomically(file_path: Path, lines: list[str]) -> None:
    """
    Replace a registry file's contents in one step that readers either see whole.

    Args:
        file_path (Path): Path to the JSONL file to rewrite.
        lines (list[str]): Raw lines to write, including their original line endings.

    Raises:
        OSError: If the file cannot be written or moved into place.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = stat.S_IMODE(file_path.stat().st_mode) if file_path.exists() else None

    # O_EXCL prevents independent writers from sharing a staging path. Create
    # staging files for existing registries privately from the start; changing
    # permissions after creation cannot protect readers that already opened it.
    # New registries use 0666 so the OS applies the current umask as before.
    creation_mode = 0o600 if existing_mode is not None else 0o666
    temp_path, temp_fd = _create_staging_file(file_path, mode=creation_mode)
    try:
        if existing_mode is not None and hasattr(os, "fchmod"):
            # Apply the registry's permissions before copying its contents so a
            # private registry never has a more readable staging copy.
            os.fchmod(temp_fd, existing_mode)

        with os.fdopen(temp_fd, "w", encoding="utf-8", newline="") as temp_file:
            temp_fd = -1
            for line in lines:
                temp_file.write(line)

        if existing_mode is not None:
            # Writing can clear special permission bits, so restore the final mode.
            os.chmod(temp_path, existing_mode)
        # The staging file is closed before replace for platforms that cannot replace
        # an open file.
        os.replace(temp_path, file_path)
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        _cleanup_staging_file(temp_path)


def replace_evaluation_results(
    *,
    file_path: Path,
    scorer_identifier: ComponentIdentifier,
    eval_hash: str,
    metrics: "ScorerMetrics",
) -> None:
    """
    Replace existing scorer metrics entry (by eval_hash) with new metrics, or add if not exists.

    This is an atomic operation that removes any existing entry with the same eval_hash
    and adds the new entry. Only one entry per eval_hash is maintained in the registry,
    ensuring we always track the highest-fidelity evaluation.

    Lines that could not be parsed are kept verbatim rather than dropped: a corrupt line
    is not an entry this call is allowed to delete, and pre-computed metrics for other
    scorers cost hours of model calls to regenerate.

    Args:
        file_path (Path): The full path to the JSONL file.
        scorer_identifier (ComponentIdentifier): The scorer's configuration identifier.
        eval_hash (str): The pre-computed evaluation hash for grouping.
        metrics (ScorerMetrics): The computed metrics (ObjectiveScorerMetrics or HarmScorerMetrics).

    Raises:
        OSError: If the registry exists but cannot be read, written, or moved into place.
    """
    # Get or create lock for this file path
    file_path_str = str(file_path)
    if file_path_str not in _file_write_locks:
        _file_write_locks[file_path_str] = threading.Lock()

    # Build new entry dictionary
    new_entry = scorer_identifier.model_dump()
    new_entry["eval_hash"] = eval_hash
    new_entry["metrics"] = _metrics_to_registry_dict(metrics)

    with _file_write_locks[file_path_str]:
        # Load existing entries, keeping track of which lines could not be parsed
        existing_lines = _read_registry_lines(file_path)

        # Keep every line that is not the entry being replaced, including unparseable ones
        preserved = [raw for raw, parsed in existing_lines if parsed is None or parsed.get("eval_hash") != eval_hash]

        # Keep the registry's existing line-ending style and only add a separator when
        # the final preserved line did not have one.
        line_ending = next(
            (ending for raw in reversed(preserved) for ending in ("\r\n", "\n", "\r") if raw.endswith(ending)),
            "\n",
        )
        output_lines = [*preserved]
        if output_lines and not output_lines[-1].endswith(("\n", "\r")):
            output_lines[-1] += line_ending
        output_lines.append(json.dumps(new_entry, allow_nan=False) + line_ending)

        # Rewrite the file with the surviving lines plus the new entry
        _rewrite_jsonl_atomically(file_path, output_lines)

        replaced = len(preserved) != len(existing_lines)
        action = "Replaced" if replaced else "Added"
        dropped = sum(1 for _, parsed in existing_lines if parsed is None)
        if dropped:
            action += f" (kept {dropped} unparseable line(s) verbatim)"
        logger.info(
            f"{action} metrics for {scorer_identifier.class_name} (eval_hash={eval_hash[:8]}...) in {file_path.name}"
        )
