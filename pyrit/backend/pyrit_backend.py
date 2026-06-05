# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
PyRIT Backend CLI - Thin wrapper around uvicorn for the PyRIT backend server.

All initialization (config loading, memory setup, initializer execution) is
handled by the FastAPI lifespan in ``pyrit.backend.main``.  This CLI simply
parses host/port/config-file/log-level/reload and starts uvicorn.

The config file path is forwarded to the app via the ``PYRIT_CONFIG_FILE``
environment variable.
"""

import logging
import os
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path
from typing import Optional

from pyrit.common.cli_helpers import CONFIG_FILE_HELP, validate_log_level_argparse


def parse_args(*, args: Optional[list[str]] = None) -> Namespace:
    """
    Parse command-line arguments for the PyRIT backend server.

    Returns:
        Namespace: Parsed command-line arguments.
    """
    parser = ArgumentParser(
        prog="pyrit_backend",
        description="""PyRIT Backend - Run the PyRIT backend API server

All configuration (database, initializers, env-files, etc.) is read from
the config file (~/.pyrit/.pyrit_conf by default, or --config-file).

Examples:
  # Start backend with default settings
  pyrit_backend

  # Start with a custom config file
  pyrit_backend --config-file ./my_config.yaml

  # Start with custom port and host
  pyrit_backend --host 0.0.0.0 --port 8080

  # Start with auto-reload for development
  pyrit_backend --reload
""",
        formatter_class=RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="Host to bind the server to (default: localhost)",
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
        help=CONFIG_FILE_HELP,
    )

    parser.add_argument(
        "--log-level",
        type=validate_log_level_argparse,
        default="WARNING",
        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) (default: WARNING)",
    )

    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development (watches for file changes)",
    )

    return parser.parse_args(args)


def main(*, args: Optional[list[str]] = None) -> int:
    """
    Start the PyRIT backend server.

    Returns:
        int: Exit code (0 for success, 1 for error).
    """
    sys.stdout.reconfigure(errors="replace")  # type: ignore[ty:unresolved-attribute]
    sys.stderr.reconfigure(errors="replace")  # type: ignore[ty:unresolved-attribute]

    try:
        parsed_args = parse_args(args=args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1

    # Forward config file to the FastAPI lifespan via env var
    if parsed_args.config_file is not None:
        os.environ["PYRIT_CONFIG_FILE"] = str(parsed_args.config_file)

    if parsed_args.host not in ("localhost", "127.0.0.1", "::1"):
        print(
            f"WARNING: Binding pyrit_backend to {parsed_args.host}:{parsed_args.port} exposes the API "
            "on a non-loopback interface. The PyRIT backend has no authentication; only do this on "
            "a trusted network.",
            file=sys.stderr,
        )

    try:
        import uvicorn

        uvicorn.run(
            "pyrit.backend.main:app",
            host=parsed_args.host,
            port=parsed_args.port,
            log_level=logging.getLevelName(parsed_args.log_level).lower()
            if isinstance(parsed_args.log_level, int)
            else parsed_args.log_level.lower(),
            reload=parsed_args.reload,
        )
        return 0
    except KeyboardInterrupt:
        print("\n🛑 Backend stopped")
        return 0
    except Exception as e:
        print(f"\nError: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
