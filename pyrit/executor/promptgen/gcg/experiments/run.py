# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Thin CLI wrapper around ``GCGGenerator.execute_async`` for AzureML jobs.

The notebook (or any user) builds a ``GCGConfig`` (strategy) and a
``GCGDataConfig`` (data) locally, serializes both with their respective
``to_json_file`` methods, ships them to Azure ML as job inputs, and the job's
command line is::

    python -m pyrit.executor.promptgen.gcg.experiments.run \\
        --config inputs/config.json \\
        --data inputs/data.json \\
        --output-dir ${{outputs.results}}

This file deserializes both configs inside the job, loads goals/targets from
the configured CSV, and runs the attack via a fresh ``GCGGenerator``.
"""

import argparse
import asyncio
import os
from dataclasses import replace
from pathlib import Path

from pyrit.executor.promptgen.gcg.config import GCGConfig, GCGDataConfig, GCGOutputConfig
from pyrit.executor.promptgen.gcg.data import load_goals_and_targets
from pyrit.executor.promptgen.gcg.generator import GCGGenerator
from pyrit.setup.initialization import _load_environment_files


def _parse_arguments() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Run a GCG attack from serialized GCGConfig + GCGDataConfig JSON files. "
            "Intended as the AzureML job entry point; for local development construct a "
            "GCGGenerator and call execute_async directly."
        )
    )
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to a JSON file produced by GCGConfig.to_json_file() (strategy).",
    )
    parser.add_argument(
        "--data",
        type=str,
        required=True,
        help="Path to a JSON file produced by GCGDataConfig.to_json_file() (CSV paths + counts).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help=(
            "Optional output directory. When set, the result file is written under this "
            "directory by overriding config.output.result_prefix. The basename of the "
            "config's existing result_prefix is preserved (or defaults to 'gcg_suffix'). "
            "AzureML jobs pass ${{outputs.<name>}} here so the result lands in the named "
            "output mount."
        ),
    )
    return parser.parse_args()


def _resolve_output(*, output: GCGOutputConfig, output_dir: str | None) -> GCGOutputConfig:
    """
    Combine ``output_dir`` with the basename of the existing result prefix.

    Returns:
        GCGOutputConfig: The resolved output configuration.
    """
    if output_dir is None:
        return output
    base = Path(output.result_prefix).name or "gcg_suffix"
    return replace(output, result_prefix=str(Path(output_dir) / base))


async def _main_async(config_path: str, data_path: str, output_dir: str | None = None) -> None:
    _load_environment_files(env_files=None)
    config = GCGConfig.from_json_file(config_path)
    data = GCGDataConfig.from_json_file(data_path)
    if config.hf_token is None:
        config.hf_token = os.environ.get("HUGGINGFACE_TOKEN")
    if not config.hf_token:
        raise ValueError(
            "No HuggingFace token available. Set GCGConfig.hf_token in the JSON or "
            "export HUGGINGFACE_TOKEN before running."
        )

    output = _resolve_output(output=config.output, output_dir=output_dir)
    generator = GCGGenerator(
        models=config.models,
        test_models=config.test_models,
        algorithm=config.algorithm,
        strategy=config.strategy,
        output=output,
        hf_token=config.hf_token,
    )
    train_goals, train_targets, test_goals, test_targets = load_goals_and_targets(
        data=data, random_seed=config.algorithm.random_seed
    )
    await generator.execute_async(
        goals=train_goals,
        targets=train_targets,
        test_goals=test_goals,
        test_targets=test_targets,
    )


if __name__ == "__main__":
    args = _parse_arguments()
    asyncio.run(_main_async(args.config, args.data, args.output_dir))
