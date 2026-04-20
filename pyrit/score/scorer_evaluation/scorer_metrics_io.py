# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Utility functions for reading/writing scorer evaluation metrics to JSONL files.
Thread-safe operations for appending entries.
"""

import json
import logging
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar

from pyrit.common.path import (
    SCORER_EVALS_PATH,
)
from pyrit.identifiers import ScorerIdentifier
from pyrit.score.scorer_evaluation.scorer_metrics import (
    HarmScorerMetrics,
    ObjectiveScorerMetrics,
    ScorerMetrics,
    ScorerMetricsWithIdentity,
)

logger = logging.getLogger(__name__)

# Thread locks for writing (module-level, persists for application lifetime)
# Locks are created per file path to ensure thread-safe writes
_file_write_locks: Dict[str, threading.Lock] = {}

M = TypeVar("M", bound=ScorerMetrics)


def _metrics_to_registry_dict(metrics: ScorerMetrics) -> Dict[str, Any]:
    """
    Convert metrics to a dictionary suitable for registry storage.

    Excludes:
    - trial_scores (too large for registry storage)
    - Internal fields starting with '_'

    Args:
        metrics (ScorerMetrics): The metrics object to convert.

    Returns:
        Dict: A dictionary with excluded fields removed.
    """
    metrics_dict = asdict(metrics)
    excluded_keys = {"trial_scores"}
    return {k: v for k, v in metrics_dict.items() if k not in excluded_keys and v is not None and not k.startswith("_")}


def get_all_objective_metrics(
    file_path: Optional[Path] = None,
) -> List[ScorerMetricsWithIdentity[ObjectiveScorerMetrics]]:
    """
    Load all objective scorer metrics with full scorer identity for comparison.

    Returns a list of ScorerMetricsWithIdentity[ObjectiveScorerMetrics] objects that wrap
    the scorer's identity information and its performance metrics, enabling clean attribute
    access like `entry.metrics.accuracy` or `entry.metrics.f1_score`.

    Args:
        file_path (Optional[Path]): Path to a specific JSONL file to load.
            If not provided, uses the default path:
            SCORER_EVALS_PATH / "objective" / "objective_achieved_metrics.jsonl"

    Returns:
        List[ScorerMetricsWithIdentity[ObjectiveScorerMetrics]]: List of metrics with scorer identity.
            Access metrics via `entry.metrics.accuracy`, `entry.metrics.f1_score`, etc.
            Access scorer info via `entry.scorer_identifier.class_name`, etc.
    """
    if file_path is None:
        file_path = SCORER_EVALS_PATH / "objective" / "objective_achieved_metrics.jsonl"

    return _load_metrics_from_file(file_path=file_path, metrics_class=ObjectiveScorerMetrics)


def get_all_harm_metrics(
    harm_category: str,
) -> List[ScorerMetricsWithIdentity[HarmScorerMetrics]]:
    """
    Load all harm scorer metrics for a specific harm category.

    Returns a list of ScorerMetricsWithIdentity[HarmScorerMetrics] objects that wrap
    the scorer's identity information and its performance metrics, enabling clean attribute
    access like `entry.metrics.mean_absolute_error` or `entry.metrics.harm_category`.

    Args:
        harm_category (str): The harm category to load metrics for (e.g., "hate_speech", "violence").

    Returns:
        List[ScorerMetricsWithIdentity[HarmScorerMetrics]]: List of metrics with scorer identity.
            Access metrics via `entry.metrics.mean_absolute_error`, `entry.metrics.harm_category`, etc.
            Access scorer info via `entry.scorer_identifier.class_name`, etc.
    """
    file_path = SCORER_EVALS_PATH / "harm" / f"{harm_category}_metrics.jsonl"
    return _load_metrics_from_file(file_path=file_path, metrics_class=HarmScorerMetrics)


def _load_metrics_from_file(
    *,
    file_path: Path,
    metrics_class: Type[M],
) -> List[ScorerMetricsWithIdentity[M]]:
    """
    Load scorer metrics from a JSONL file with the specified metrics class.

    This is a private helper function used by get_all_objective_metrics and get_all_harm_metrics.

    Args:
        file_path (Path): Path to the JSONL file to load.
        metrics_class (Type[M]): The metrics class to instantiate (ObjectiveScorerMetrics or HarmScorerMetrics).

    Returns:
        List[ScorerMetricsWithIdentity[M]]: List of metrics with scorer identity.
    """
    results: List[ScorerMetricsWithIdentity[M]] = []
    entries = _load_jsonl(file_path)

    for entry in entries:
        metrics_dict = entry.get("metrics", {})
        # Filter out internal fields that have init=False (e.g., _harm_definition_obj)
        metrics_dict = {k: v for k, v in metrics_dict.items() if not k.startswith("_")}

        # Extract scorer identity (everything except metrics)
        identity_dict = {k: v for k, v in entry.items() if k != "metrics"}

        try:
            # Reconstruct ScorerIdentifier from the stored dict
            scorer_identifier = ScorerIdentifier.from_dict(identity_dict)

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


def find_objective_metrics_by_hash(
    *,
    hash: str,
    file_path: Optional[Path] = None,
) -> Optional[ObjectiveScorerMetrics]:
    """
    Find objective scorer metrics by configuration hash.

    Args:
        hash (str): The scorer configuration hash to search for.
        file_path (Optional[Path]): Path to the JSONL file to search.
            If not provided, uses the default path:
            SCORER_EVALS_PATH / "objective" / "objective_achieved_metrics.jsonl"

    Returns:
        ObjectiveScorerMetrics if found, else None.
    """
    if file_path is None:
        file_path = SCORER_EVALS_PATH / "objective" / "objective_achieved_metrics.jsonl"

    return _find_metrics_by_hash(file_path=file_path, hash=hash, metrics_class=ObjectiveScorerMetrics)


def find_harm_metrics_by_hash(
    *,
    hash: str,
    harm_category: str,
) -> Optional[HarmScorerMetrics]:
    """
    Find harm scorer metrics by configuration hash.

    Args:
        hash (str): The scorer configuration hash to search for.
        harm_category (str): The harm category to search in (e.g., "hate_speech", "violence").

    Returns:
        HarmScorerMetrics if found, else None.
    """
    file_path = SCORER_EVALS_PATH / "harm" / f"{harm_category}_metrics.jsonl"
    return _find_metrics_by_hash(file_path=file_path, hash=hash, metrics_class=HarmScorerMetrics)


def _find_metrics_by_hash(
    *,
    file_path: Path,
    hash: str,
    metrics_class: Type[M],
) -> Optional[M]:
    """
    Find scorer metrics by configuration hash in a specific file.

    This is a private helper function used by find_objective_metrics_by_hash and find_harm_metrics_by_hash.

    Args:
        file_path (Path): Path to the JSONL file to search.
        hash (str): The scorer configuration hash to search for.
        metrics_class (Type[M]): The metrics class to instantiate.

    Returns:
        The metrics instance if found, else None.
    """
    entries = _load_jsonl(file_path)

    for entry in entries:
        if entry.get("hash") == hash:
            metrics_dict = entry.get("metrics", {})
            # Filter out internal fields that have init=False (e.g., _harm_definition_obj)
            metrics_dict = {k: v for k, v in metrics_dict.items() if not k.startswith("_")}
            try:
                return metrics_class(**metrics_dict)
            except Exception as e:
                logger.warning(f"Failed to parse metrics for hash {hash}: {e}")
                return None

    return None


def add_evaluation_results(
    *,
    file_path: Path,
    scorer_identifier: ScorerIdentifier,
    metrics: "ScorerMetrics",
) -> None:
    """
    Append scorer metrics entry to the specified evaluation results file (thread-safe).

    This unified function handles both objective and harm scorer metrics, writing to
    the specified file path with appropriate validation and thread safety.

    Args:
        file_path (Path): The full path to the JSONL file to append to.
        scorer_identifier (ScorerIdentifier): The scorer's configuration identifier.
        metrics (ScorerMetrics): The computed metrics (ObjectiveScorerMetrics or HarmScorerMetrics).
    """
    # Get or create lock for this file path
    file_path_str = str(file_path)
    if file_path_str not in _file_write_locks:
        _file_write_locks[file_path_str] = threading.Lock()

    # Build entry dictionary
    entry = scorer_identifier.to_dict()
    entry["metrics"] = _metrics_to_registry_dict(metrics)

    # Write to file with thread safety
    _append_jsonl_entry(
        file_path=file_path,
        lock=_file_write_locks[file_path_str],
        entry=entry,
    )

    logger.info(f"Added metrics for {scorer_identifier.class_name} to {file_path.name}")


def _load_jsonl(file_path: Path) -> List[Dict[str, Any]]:
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
        with open(file_path, "r", encoding="utf-8") as f:
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


def _append_jsonl_entry(file_path: Path, lock: threading.Lock, entry: Dict[str, Any]) -> None:
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
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write to registry {file_path}: {e}")
            raise


def replace_evaluation_results(
    *,
    file_path: Path,
    scorer_identifier: ScorerIdentifier,
    metrics: "ScorerMetrics",
) -> None:
    """
    Replace existing scorer metrics entry (by hash) with new metrics, or add if not exists.

    This is an atomic operation that removes any existing entry with the same scorer hash
    and adds the new entry. Only one entry per scorer hash is maintained in the registry,
    ensuring we always track the highest-fidelity evaluation.

    Args:
        file_path (Path): The full path to the JSONL file.
        scorer_identifier (ScorerIdentifier): The scorer's configuration identifier.
        metrics (ScorerMetrics): The computed metrics (ObjectiveScorerMetrics or HarmScorerMetrics).
    """
    # Get or create lock for this file path
    file_path_str = str(file_path)
    if file_path_str not in _file_write_locks:
        _file_write_locks[file_path_str] = threading.Lock()

    scorer_hash = scorer_identifier.hash

    # Build new entry dictionary
    new_entry = scorer_identifier.to_dict()
    new_entry["metrics"] = _metrics_to_registry_dict(metrics)

    with _file_write_locks[file_path_str]:
        try:
            # Load existing entries
            existing_entries = _load_jsonl(file_path)

            # Filter out entries with the same hash
            filtered_entries = [e for e in existing_entries if e.get("hash") != scorer_hash]

            # Add the new entry
            filtered_entries.append(new_entry)

            # Rewrite the file atomically
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                for entry in filtered_entries:
                    f.write(json.dumps(entry) + "\n")

            replaced = len(existing_entries) != len(filtered_entries)
            action = "Replaced" if replaced else "Added"
            logger.info(
                f"{action} metrics for {scorer_identifier.class_name} (hash={scorer_hash[:8]}...) in {file_path.name}"
            )

        except Exception as e:
            logger.error(f"Failed to replace entry in registry {file_path}: {e}")
            raise
