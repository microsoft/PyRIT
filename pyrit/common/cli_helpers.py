# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Lightweight CLI helpers shared between the backend launcher (``pyrit_backend``)
and the thin REST CLI (``pyrit_scan`` / ``pyrit_shell``).

This module intentionally has no heavy pyrit imports so it can be loaded by
either entry point without dragging in unrelated subsystems.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

CONFIG_FILE_HELP = (
    "Path to a YAML configuration file. Allows specifying database, initializers (with args), "
    "initialization scripts, and env files. CLI arguments override config file values. "
    "If not specified, ~/.pyrit/.pyrit_conf is loaded if it exists."
)


def validate_log_level(*, log_level: str) -> int:
    """
    Validate a log level string and convert it to a ``logging`` constant.

    Args:
        log_level: Log level string (case-insensitive).

    Returns:
        Validated log level as a ``logging`` module constant (e.g. ``logging.WARNING``).

    Raises:
        ValueError: If ``log_level`` is not one of DEBUG/INFO/WARNING/ERROR/CRITICAL.
    """
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    level_upper = log_level.upper()
    if level_upper not in valid_levels:
        raise ValueError(f"Invalid log level: {log_level}. Must be one of: {', '.join(valid_levels)}")
    level_value: int = getattr(logging, level_upper)
    return level_value


def validate_log_level_argparse(value: Any) -> int:
    """
    Argparse-compatible wrapper around ``validate_log_level``.

    Adapts the keyword-only validator to argparse's positional ``type=`` calling
    convention and converts ``ValueError`` to ``argparse.ArgumentTypeError``.

    Args:
        value: Log level string supplied by argparse.

    Returns:
        Validated log level as a ``logging`` module constant.

    Raises:
        argparse.ArgumentTypeError: If ``value`` is not a valid log level.
    """
    try:
        return validate_log_level(log_level=value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
