# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
PyRIT Backend CLI - Command-line interface for running the PyRIT backend server.

This module provides the main entry point for the pyrit_backend command.
"""

import asyncio
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path
from typing import Optional

from pyrit.cli import frontend_core


def parse_args(*, args: Optional[list[str]] = None) -> Namespace:
    """
    Parse command-line arguments for the PyRIT backend server.

    Returns:
        Namespace: Parsed command-line arguments.
    """
    parser = ArgumentParser(
        prog="pyrit_backend",
        description="""PyRIT Backend - Run the PyRIT backend API server

Examples:
  # Start backend with default settings
  pyrit_backend

  # Start with built-in initializers
  pyrit_backend --initializers airt

  # Start with custom initialization scripts
  pyrit_backend --initialization-scripts ./my_targets.py

  # Start with custom port and host
  pyrit_backend --host 0.0.0.0 --port 8080

  # List available initializers
  pyrit_backend --list-initializers
""",
        formatter_class=RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind the server to (default: 0.0.0.0)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind the server to (default: 8000)",
    )

    parser.add_argument(
        "--config-file",
        type=Path,
        help=frontend_core.ARG_HELP["config_file"],
    )

    parser.add_argument(
        "--log-level",
        type=frontend_core.validate_log_level_argparse,
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) (default: INFO)",
    )

    parser.add_argument(
        "--list-initializers",
        action="store_true",
        help="List all available initializers and exit",
    )

    parser.add_argument(
        "--database",
        type=frontend_core.validate_database_argparse,
        default=None,
        help=(
            f"Database type to use for memory storage ({frontend_core.IN_MEMORY}, "
            f"{frontend_core.SQLITE}, {frontend_core.AZURE_SQL}). "
            f"Defaults to value from config file, or {frontend_core.SQLITE} if not specified."
        ),
    )

    parser.add_argument(
        "--initializers",
        type=frontend_core._parse_initializer_arg,
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
        "--reload",
        action="store_true",
        help="Enable auto-reload for development (watches for file changes)",
    )

    return parser.parse_args(args)


async def initialize_and_run_async(*, parsed_args: Namespace) -> int:
    """
    Initialize PyRIT and start the backend server.

    Returns:
        int: Exit code (0 for success, 1 for error).
    """
    from pyrit.setup import initialize_pyrit_async

    # Resolve initialization scripts if provided
    initialization_scripts = None
    if parsed_args.initialization_scripts:
        try:
            initialization_scripts = frontend_core.resolve_initialization_scripts(
                script_paths=parsed_args.initialization_scripts
            )
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1

    # Resolve env files if provided
    env_files = None
    if parsed_args.env_files:
        try:
            env_files = frontend_core.resolve_env_files(env_file_paths=parsed_args.env_files)
        except ValueError as e:
            print(f"Error: {e}")
            return 1

    # Create context using FrontendCore (handles config file merging)
    context = frontend_core.FrontendCore(
        config_file=parsed_args.config_file,
        database=parsed_args.database,
        initialization_scripts=initialization_scripts,
        initializer_names=parsed_args.initializers,
        env_files=env_files,
        log_level=parsed_args.log_level,
    )

    # Initialize PyRIT (loads registries, sets up memory)
    print("🔧 Initializing PyRIT...")
    await context.initialize_async()

    # Run initializers up-front (backend runs them once at startup, not per-scenario)
    initializer_instances = None
    if context._initializer_configs:
        print(f"Running {len(context._initializer_configs)} initializer(s)...")
        initializer_instances = []
        for config in context._initializer_configs:
            initializer_class = context.initializer_registry.get_class(config.name)
            instance = initializer_class()
            if config.args:
                instance.set_params_from_args(args=config.args)
            initializer_instances.append(instance)

        # Re-initialize with initializers applied
        await initialize_pyrit_async(
            memory_db_type=context._database,
            initialization_scripts=context._initialization_scripts,
            initializers=initializer_instances,
            env_files=context._env_files,
        )

    # Start uvicorn server
    import uvicorn

    from pyrit.backend.main import app

    # Expose configured default labels to the version endpoint
    default_labels: dict[str, str] = {}
    if context._operator:
        default_labels["operator"] = context._operator
    if context._operation:
        default_labels["operation"] = context._operation
    app.state.default_labels = default_labels

    print(f"🚀 Starting PyRIT backend on http://{parsed_args.host}:{parsed_args.port}")
    print(f"   API Docs: http://{parsed_args.host}:{parsed_args.port}/docs")

    uvicorn_config = uvicorn.Config(
        "pyrit.backend.main:app",
        host=parsed_args.host,
        port=parsed_args.port,
        log_level=parsed_args.log_level,
        reload=parsed_args.reload,
    )
    server = uvicorn.Server(uvicorn_config)
    await server.serve()

    return 0


def main(*, args: Optional[list[str]] = None) -> int:
    """
    Start the PyRIT backend server CLI.

    Returns:
        int: Exit code (0 for success, 1 for error).
    """
    # Ensure emoji and other Unicode characters don't crash on Windows consoles
    # that use legacy encodings like cp1252.
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(errors="replace")  # type: ignore[union-attr]

    try:
        parsed_args = parse_args(args=args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1

    # Handle list-initializers command
    if parsed_args.list_initializers:
        context = frontend_core.FrontendCore(config_file=parsed_args.config_file, log_level=parsed_args.log_level)
        scenarios_path = frontend_core.get_default_initializer_discovery_path()
        return asyncio.run(frontend_core.print_initializers_list_async(context=context, discovery_path=scenarios_path))

    # Run the server
    try:
        return asyncio.run(initialize_and_run_async(parsed_args=parsed_args))
    except KeyboardInterrupt:
        print("\n🛑 Backend stopped")
        return 0
    except Exception as e:
        print(f"\nError: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
