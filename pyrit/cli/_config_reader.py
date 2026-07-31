# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Lightweight config reader for the PyRIT CLI thin client.

Reads only the ``server.url`` field from ``~/.pyrit/.pyrit_conf`` (and an
optional overlay file) using ``yaml.safe_load``.  No heavy pyrit imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Mirror the default path from pyrit.common.path without importing it.
_DEFAULT_CONFIG_DIR = Path.home() / ".pyrit"
_DEFAULT_CONFIG_FILE = _DEFAULT_CONFIG_DIR / ".pyrit_conf"

DEFAULT_SERVER_URL = "http://localhost:8000"


class ConfigError(Exception):
    """
    Raised when a CLI config file exists but cannot be parsed or is structurally
    invalid (e.g. malformed YAML, a non-mapping root, or a wrong-typed
    ``server.url``).

    A *missing* config file or a *missing* field is not an error -- the CLI just
    falls back to its defaults. This is reserved for configs the user clearly
    intended to set but got wrong, so callers can surface a clear message instead
    of silently using the default.
    """


def _load_config_mapping(*, path: Path, yaml_module: Any) -> dict | None:
    """
    Load a single YAML config file and return its top-level mapping.

    Args:
        path (Path): YAML config file path (assumed to exist).
        yaml_module (Any): The imported ``yaml`` module (passed to avoid a
            top-level import).

    Returns:
        dict | None: The parsed mapping, or ``None`` for an empty file.

    Raises:
        ConfigError: If the file cannot be read, is not valid YAML, or has a
            non-mapping top-level value.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml_module.safe_load(fh)
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc
    except yaml_module.YAMLError as exc:
        raise ConfigError(f"Config file {path} is not valid YAML: {exc}") from exc

    if data is None:
        return None
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a top-level mapping, got {type(data).__name__}.")
    return data


def read_server_url(*, config_file: Path | None = None) -> str | None:
    """
    Read ``server.url`` from the default config and an optional overlay.

    Layers (later wins):
      1. ``~/.pyrit/.pyrit_conf`` (if it exists)
      2. *config_file* (if provided and exists)

    Args:
        config_file (Path | None): Optional explicit config path.

    Returns:
        str | None: The server URL, or ``None`` if not configured.

    Raises:
        ConfigError: If a config file exists but is malformed.
    """
    import yaml

    paths: list[Path] = []
    if _DEFAULT_CONFIG_FILE.exists():
        paths.append(_DEFAULT_CONFIG_FILE)
    if config_file is not None and config_file.exists():
        paths.append(config_file)

    url: str | None = None
    for p in paths:
        url = _extract_server_url(path=p, yaml_module=yaml) or url
    return url


def validate_client_config(*, config_file: Path | None = None) -> None:
    """
    Validate that layered config files do not contain removed client options.

    Args:
        config_file: Optional overlay path; the default ``~/.pyrit/.pyrit_conf``
            is always checked when present.

    Raises:
        ConfigError: If a config file is malformed or contains a removed option.
    """
    import yaml

    paths: list[Path] = []
    if _DEFAULT_CONFIG_FILE.exists():
        paths.append(_DEFAULT_CONFIG_FILE)
    if config_file is not None and config_file.exists():
        paths.append(config_file)

    for p in paths:
        data = _load_config_mapping(path=p, yaml_module=yaml)
        if data is None:
            continue
        if "scenario" in data:
            raise ConfigError(
                f"Config file {p}: 'scenario' is no longer supported. "
                "Pass the scenario name positionally and its parameters as CLI flags."
            )


def _extract_server_url(*, path: Path, yaml_module: Any) -> str | None:
    """
    Extract ``server.url`` from a single YAML file.

    Args:
        path (Path): YAML config file path.
        yaml_module (Any): The imported ``yaml`` module (passed to avoid
            top-level import).

    Returns:
        str | None: The URL string, or ``None`` if absent.

    Raises:
        ConfigError: If the file is malformed, or ``server`` / ``server.url``
            are present but have the wrong type.
    """
    data = _load_config_mapping(path=path, yaml_module=yaml_module)
    if data is None:
        return None

    server_block = data.get("server")
    if server_block is None:
        return None
    if not isinstance(server_block, dict):
        raise ConfigError(
            f"Config file {path}: 'server' must be a mapping with a 'url' field, got {type(server_block).__name__}."
        )

    raw_url = server_block.get("url")
    if raw_url is None:
        return None
    if not isinstance(raw_url, str):
        raise ConfigError(f"Config file {path}: 'server.url' must be a string, got {type(raw_url).__name__}.")

    return raw_url.strip() or None
