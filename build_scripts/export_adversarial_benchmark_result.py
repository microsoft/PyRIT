# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Export readable partial or completed adversarial benchmark results from SQLite."""

import argparse
import asyncio
import contextlib
from pathlib import Path

from pyrit.cli._output import print_attacks_table
from pyrit.cli._results import build_attacks_table_payload
from pyrit.memory import CentralMemory
from pyrit.models import ScenarioResult
from pyrit.output.scenario_result.pretty import PrettyScenarioResultMemoryPrinter
from pyrit.output.sink import FileSink
from pyrit.setup import SQLITE, initialize_pyrit_async


async def _load_result_async(*, scenario_result_id: str) -> ScenarioResult:
    """Load one persisted scenario result, regardless of terminal state."""
    await initialize_pyrit_async(
        memory_db_type=SQLITE,
        load_defaults=False,
        env_files=[],
        silent=True,
    )
    results = CentralMemory.get_memory_instance().get_scenario_results(
        scenario_result_ids=[scenario_result_id],
    )
    if not results:
        raise ValueError(f"Scenario result '{scenario_result_id}' was not found in SQLite memory.")
    return results[0]


async def _write_overview_async(*, result: ScenarioResult, output_dir: Path) -> None:
    """Write the existing scenario overview without terminal color codes."""
    printer = PrettyScenarioResultMemoryPrinter(
        sink=FileSink(path=output_dir / "overview.txt"),
        enable_colors=False,
    )
    await printer.write_async(result)


def _write_attacks(*, result: ScenarioResult, output_dir: Path) -> None:
    """Write machine-readable and console-style partial attack tables."""
    payload = build_attacks_table_payload(
        result=result,
        scenario_result_id=str(result.id),
    )
    (output_dir / "attacks.json").write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    with open(output_dir / "attacks.txt", "w", encoding="utf-8") as output:
        with contextlib.redirect_stdout(output):
            print_attacks_table(payload=payload)


async def _export_async(*, scenario_result_id: str, output_dir: Path) -> None:
    """Export all readable result views."""
    result = await _load_result_async(scenario_result_id=scenario_result_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    await _write_overview_async(result=result, output_dir=output_dir)
    await asyncio.to_thread(_write_attacks, result=result, output_dir=output_dir)


def main() -> None:
    """Run the result exporter."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-result-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(
        _export_async(
            scenario_result_id=args.scenario_result_id,
            output_dir=args.output_dir,
        )
    )


if __name__ == "__main__":
    main()
