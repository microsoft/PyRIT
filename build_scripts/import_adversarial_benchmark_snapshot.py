# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Upsert an already-exported ``technique-metrics.json`` snapshot into the committed benchmark store.

Use this when the technique-metrics rows were produced somewhere this checkout can't reach directly
— for example a ``technique-metrics.json`` downloaded from an Azure DevOps
``adversarial-benchmark-<BuildId>`` pipeline artifact (see ``.azuredevops/adversarial-benchmark.yml``)
— so there's no local SQLite memory holding the underlying scenario result.
``build_scripts.export_adversarial_benchmark_result --update-benchmark-store`` remains the right tool
when the scenario result *is* available locally; this script only merges a snapshot that already went
through that exporter elsewhere.

Snapshots produced before ``objective_target``/``objective_scorer``/``dataset`` existed on the
exporter (e.g. a ``main``-branch pipeline run predating this dashboard's schema) won't carry those
fields at all. Pass ``--default-objective-target``/``--default-objective-scorer``/``--default-dataset``
to backfill whichever ones are missing; a row that already has a field keeps its own value.

Example:
    python -m build_scripts.import_adversarial_benchmark_snapshot \\
        --technique-metrics-json ./adversarial-benchmark-16150/technique-metrics.json \\
        --default-objective-target openai_chat \\
        --default-dataset adversarial_benchmark_v1
"""

import argparse
import json
from pathlib import Path
from typing import Any

from build_scripts.export_adversarial_benchmark_result import DEFAULT_BENCHMARK_STORE_PATH, upsert_benchmark_metrics

#: Fallback label for an identity field that's missing from the source snapshot and wasn't
#: backfilled via a ``--default-*`` flag. Mirrors the fallback
#: ``pyrit.scenario.scenarios.benchmark.adversarial.resolve_objective_identity`` uses for an
#: unresolved identifier, so an unlabeled row reads the same way here as it would coming
#: straight out of the exporter.
_UNKNOWN_IDENTITY = "<unknown>"

#: The identity fields ``upsert_benchmark_metrics`` requires on every row (see
#: ``_BENCHMARK_METRICS_KEY_FIELDS``) that older snapshots may not carry.
_IDENTITY_FIELDS = ("objective_target", "objective_scorer", "dataset")


def _load_metrics(*, technique_metrics_json: Path) -> list[dict[str, Any]]:
    """Read and validate a previously exported technique-metrics.json file."""
    metrics = json.loads(technique_metrics_json.read_text(encoding="utf-8"))
    if not isinstance(metrics, list):
        raise ValueError(f"{technique_metrics_json} must contain a JSON list of technique-metrics rows.")
    return metrics


def _fill_missing_identity(
    *, metrics: list[dict[str, Any]], identity_defaults: dict[str, str | None]
) -> list[dict[str, Any]]:
    """
    Backfill identity fields that are absent from a row, without touching fields it already has.

    Older snapshots (e.g. from an exporter version that predates ``objective_target``/
    ``objective_scorer``/``dataset``) may be missing some or all of these fields entirely.
    ``upsert_benchmark_metrics`` requires all of them to compute its upsert key, so each missing
    field is set to the matching ``identity_defaults`` value if one was supplied, or to
    ``_UNKNOWN_IDENTITY`` otherwise. A field a row already has is never overwritten.

    Args:
        metrics (list[dict[str, Any]]): Technique-metrics rows to backfill.
        identity_defaults (dict[str, str | None]): Optional default value for each of
            ``_IDENTITY_FIELDS``, keyed by field name.

    Returns:
        list[dict[str, Any]]: New row dicts with every identity field present.
    """
    filled: list[dict[str, Any]] = []
    for row in metrics:
        row = dict(row)
        for field in _IDENTITY_FIELDS:
            if not row.get(field):
                row[field] = identity_defaults.get(field) or _UNKNOWN_IDENTITY
        filled.append(row)
    return filled


def import_benchmark_snapshot(
    *,
    technique_metrics_json: Path,
    benchmark_store_path: Path | None = None,
    default_objective_target: str | None = None,
    default_objective_scorer: str | None = None,
    default_dataset: str | None = None,
) -> int:
    """
    Upsert a technique-metrics.json snapshot into the committed benchmark metrics store.

    Args:
        technique_metrics_json (Path): Path to a ``technique-metrics.json`` file, as written by
            ``export_adversarial_benchmark_result.py``.
        benchmark_store_path (Path | None): Override for the committed store path. Defaults to
            ``DEFAULT_BENCHMARK_STORE_PATH``.
        default_objective_target (str | None): Value to backfill onto any row missing
            ``objective_target``. Leaves the field untouched on rows that already have it.
        default_objective_scorer (str | None): Value to backfill onto any row missing
            ``objective_scorer``. Leaves the field untouched on rows that already have it.
        default_dataset (str | None): Value to backfill onto any row missing ``dataset``. Leaves
            the field untouched on rows that already have it.

    Returns:
        int: The number of rows upserted.
    """
    metrics = _load_metrics(technique_metrics_json=technique_metrics_json)
    metrics = _fill_missing_identity(
        metrics=metrics,
        identity_defaults={
            "objective_target": default_objective_target,
            "objective_scorer": default_objective_scorer,
            "dataset": default_dataset,
        },
    )
    store_path = benchmark_store_path or DEFAULT_BENCHMARK_STORE_PATH
    upsert_benchmark_metrics(metrics=metrics, store_path=store_path)
    return len(metrics)


def main() -> int:
    """Run the snapshot import."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--technique-metrics-json",
        type=Path,
        required=True,
        help="Path to a technique-metrics.json file exported from a benchmark run.",
    )
    parser.add_argument(
        "--benchmark-store-path",
        type=Path,
        default=None,
        help=f"Override the committed benchmark metrics store path (default: {DEFAULT_BENCHMARK_STORE_PATH}).",
    )
    parser.add_argument(
        "--default-objective-target",
        default=None,
        help="Backfill onto any row missing 'objective_target' (rows that already have it are left alone).",
    )
    parser.add_argument(
        "--default-objective-scorer",
        default=None,
        help="Backfill onto any row missing 'objective_scorer' (rows that already have it are left alone).",
    )
    parser.add_argument(
        "--default-dataset",
        default=None,
        help="Backfill onto any row missing 'dataset' (rows that already have it are left alone).",
    )
    args = parser.parse_args()
    try:
        count = import_benchmark_snapshot(
            technique_metrics_json=args.technique_metrics_json,
            benchmark_store_path=args.benchmark_store_path,
            default_objective_target=args.default_objective_target,
            default_objective_scorer=args.default_objective_scorer,
            default_dataset=args.default_dataset,
        )
    except Exception as error:
        parser.exit(1, f"Import failed: {error}\n")
    store_path = args.benchmark_store_path or DEFAULT_BENCHMARK_STORE_PATH
    print(f"Upserted {count} row(s) into {store_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
