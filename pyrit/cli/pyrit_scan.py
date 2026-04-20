# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
PyRIT CLI - Command-line interface for running security scenarios.

This module provides the main entry point for the pyrit_scan command.
"""

import asyncio
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from typing import Optional

from pyrit.cli import frontend_core


def parse_args(args: Optional[list[str]] = None) -> Namespace:
    """
    Parse command-line arguments for the PyRIT scanner.

    Returns:
        Namespace: Parsed command-line arguments.
    """
    parser = ArgumentParser(
        prog="pyrit_scan",
        description="""PyRIT Scanner - Run security scenarios against AI systems

Examples:
  # List available scenarios and initializers
  pyrit_scan --list-scenarios
  pyrit_scan --list-initializers

  # Run a scenario with built-in initializers
  pyrit_scan foundry --initializers openai_objective_target load_default_datasets

  # Run with custom initialization scripts
  pyrit_scan garak.encoding --initialization-scripts ./my_config.py

  # Run specific strategies or options
  pyrit scan foundry --strategies base64 rot13 --initializers openai_objective_target
  pyrit_scan foundry --initializers openai_objective_target --max-concurrency 10 --max-retries 3
  pyrit_scan garak.encoding --initializers openai_objective_target --memory-labels '{"run_id":"test123"}'
""",
        formatter_class=RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--log-level",
        type=frontend_core.validate_log_level_argparse,
        default="WARNING",
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) (default: WARNING)",
    )

    parser.add_argument(
        "--list-scenarios",
        action="store_true",
        help="List all available scenarios and exit",
    )

    parser.add_argument(
        "--list-initializers",
        action="store_true",
        help="List all available scenario initializers and exit",
    )

    parser.add_argument(
        "scenario_name",
        type=str,
        nargs="?",
        help="Name of the scenario to run",
    )

    parser.add_argument(
        "--database",
        type=frontend_core.validate_database_argparse,
        default=frontend_core.SQLITE,
        help=(
            f"Database type to use for memory storage ({frontend_core.IN_MEMORY}, "
            f"{frontend_core.SQLITE}, {frontend_core.AZURE_SQL}) (default: {frontend_core.SQLITE})"
        ),
    )

    parser.add_argument(
        "--initializers",
        type=str,
        nargs="+",
        help=frontend_core.ARG_HELP["initializers"],
    )

    parser.add_argument(
        "--initialization-scripts",
        type=str,
        nargs="+",
        help=frontend_core.ARG_HELP["initialization_scripts"],
    )

    parser.add_argument(
        "--env-files",
        type=str,
        nargs="+",
        help=frontend_core.ARG_HELP["env_files"],
    )

    parser.add_argument(
        "--strategies",
        "-s",
        type=str,
        nargs="+",
        dest="scenario_strategies",
        help=frontend_core.ARG_HELP["scenario_strategies"],
    )

    parser.add_argument(
        "--max-concurrency",
        type=frontend_core.positive_int,
        help=frontend_core.ARG_HELP["max_concurrency"],
    )

    parser.add_argument(
        "--max-retries",
        type=frontend_core.non_negative_int,
        help=frontend_core.ARG_HELP["max_retries"],
    )

    parser.add_argument(
        "--memory-labels",
        type=str,
        help=frontend_core.ARG_HELP["memory_labels"],
    )

    parser.add_argument(
        "--dataset-names",
        type=str,
        nargs="+",
        help=frontend_core.ARG_HELP["dataset_names"],
    )

    parser.add_argument(
        "--max-dataset-size",
        type=frontend_core.positive_int,
        help=frontend_core.ARG_HELP["max_dataset_size"],
    )

    return parser.parse_args(args)


def main(args: Optional[list[str]] = None) -> int:
    """
    Start the PyRIT scanner CLI.

    Returns:
        int: Exit code (0 for success, 1 for error).
    """
    print("Starting PyRIT...")
    sys.stdout.flush()

    try:
        parsed_args = parse_args(args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1

    # Handle list commands (don't need full context)
    if parsed_args.list_scenarios:
        # Simple context just for listing
        initialization_scripts = None
        if parsed_args.initialization_scripts:
            try:
                initialization_scripts = frontend_core.resolve_initialization_scripts(
                    script_paths=parsed_args.initialization_scripts
                )
            except FileNotFoundError as e:
                print(f"Error: {e}")
                return 1

        env_files = None
        if parsed_args.env_files:
            try:
                env_files = frontend_core.resolve_env_files(env_file_paths=parsed_args.env_files)
            except ValueError as e:
                print(f"Error: {e}")
                return 1

        context = frontend_core.FrontendCore(
            database=parsed_args.database,
            initialization_scripts=initialization_scripts,
            env_files=env_files,
            log_level=parsed_args.log_level,
        )

        return asyncio.run(frontend_core.print_scenarios_list_async(context=context))

    if parsed_args.list_initializers:
        # Discover from scenarios directory
        scenarios_path = frontend_core.get_default_initializer_discovery_path()

        context = frontend_core.FrontendCore(log_level=parsed_args.log_level)
        return asyncio.run(frontend_core.print_initializers_list_async(context=context, discovery_path=scenarios_path))

    # Verify scenario was provided
    if not parsed_args.scenario_name:
        print("Error: No scenario specified. Use --help for usage information.")
        return 1

    # Run scenario
    try:
        # Collect initialization scripts
        initialization_scripts = None
        if parsed_args.initialization_scripts:
            initialization_scripts = frontend_core.resolve_initialization_scripts(
                script_paths=parsed_args.initialization_scripts
            )

        # Collect environment files
        env_files = None
        if parsed_args.env_files:
            env_files = frontend_core.resolve_env_files(env_file_paths=parsed_args.env_files)

        # Create context with initializers
        context = frontend_core.FrontendCore(
            database=parsed_args.database,
            initialization_scripts=initialization_scripts,
            initializer_names=parsed_args.initializers,
            env_files=env_files,
            log_level=parsed_args.log_level,
        )

        # Parse memory labels if provided
        memory_labels = None
        if parsed_args.memory_labels:
            memory_labels = frontend_core.parse_memory_labels(json_string=parsed_args.memory_labels)

        # Run scenario
        asyncio.run(
            frontend_core.run_scenario_async(
                scenario_name=parsed_args.scenario_name,
                context=context,
                scenario_strategies=parsed_args.scenario_strategies,
                max_concurrency=parsed_args.max_concurrency,
                max_retries=parsed_args.max_retries,
                memory_labels=memory_labels,
                dataset_names=parsed_args.dataset_names,
                max_dataset_size=parsed_args.max_dataset_size,
            )
        )
        return 0

    except Exception as e:
        print(f"\nError: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
