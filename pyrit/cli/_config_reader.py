# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Lightweight config reader for the PyRIT CLI thin client.

Reads the ``server`` settings used by the client from ``~/.pyrit/.pyrit_conf``
(and an optional overlay file) using ``yaml.safe_load``. No heavy pyrit imports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyrit.cli._auth import AUTH_MODES, AuthMode

# Mirror the default path from pyrit.common.path without importing it.
_DEFAULT_CONFIG_DIR = Path.home() / ".pyrit"
_DEFAULT_CONFIG_FILE = _DEFAULT_CONFIG_DIR / ".pyrit_conf"

DEFAULT_SERVER_URL = "http://localhost:8000"
DEFAULT_SERVER_STARTUP_TIMEOUT = 120.0
DEFAULT_AUTH_MODE: AuthMode = "auto"


@dataclass(frozen=True)
class ServerSettings:
    """Client-side settings for connecting to or launching a backend server."""

    url: str | None = None
    startup_timeout: float = DEFAULT_SERVER_STARTUP_TIMEOUT
    auth_mode: AuthMode = DEFAULT_AUTH_MODE


class ConfigError(Exception):
    """
    Raised when a CLI config file exists but cannot be parsed or is structurally
    invalid (e.g. malformed YAML, a non-mapping root, or a wrong-typed
    ``server`` setting).

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
    return read_server_settings(config_file=config_file).url


def read_server_settings(*, config_file: Path | None = None) -> ServerSettings:
    """
    Read client-side server settings from the default config and an optional overlay.

    Fields in a later ``server`` block override matching earlier fields. Omitted
    fields retain their prior values, while ``server: null`` resets all settings.

    Args:
        config_file: Optional explicit config path.

    Returns:
        ServerSettings: The resolved URL, startup timeout, and authentication mode.

    Raises:
        ConfigError: If a config file exists but is malformed.
    """
    import yaml

    paths: list[Path] = []
    if _DEFAULT_CONFIG_FILE.exists():
        paths.append(_DEFAULT_CONFIG_FILE)
    if config_file is not None and config_file.exists():
        paths.append(config_file)

    settings = ServerSettings()
    for p in paths:
        data = _load_config_mapping(path=p, yaml_module=yaml)
        if data is None or "server" not in data:
            continue
        settings = _merge_server_settings(settings=settings, data=data, path=p)
    return settings


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


def _merge_server_settings(*, settings: ServerSettings, data: dict[str, Any], path: Path) -> ServerSettings:
    """
    Merge one parsed ``server`` block into resolved client settings.

    Args:
        settings: Settings resolved from earlier configuration layers.
        data: Parsed top-level config mapping.
        path: YAML config file path used in error messages.

    Returns:
        ServerSettings: Settings after applying this config layer.

    Raises:
        ConfigError: If ``server`` or one of its supported fields has the wrong type.
    """
    server_block = data.get("server")
    if server_block is None:
        return ServerSettings()
    if not isinstance(server_block, dict):
        raise ConfigError(f"Config file {path}: 'server' must be a mapping, got {type(server_block).__name__}.")

    url = settings.url
    if "url" in server_block:
        raw_url = server_block["url"]
        if raw_url is not None and not isinstance(raw_url, str):
            raise ConfigError(f"Config file {path}: 'server.url' must be a string, got {type(raw_url).__name__}.")
        url = raw_url.strip() or None if isinstance(raw_url, str) else None

    startup_timeout = settings.startup_timeout
    if "startup_timeout" in server_block:
        raw_timeout = server_block["startup_timeout"]
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, int | float)
            or not math.isfinite(raw_timeout)
            or raw_timeout <= 0
        ):
            raise ConfigError(f"Config file {path}: 'server.startup_timeout' must be a finite number greater than 0.")
        startup_timeout = float(raw_timeout)

    auth_mode = settings.auth_mode
    if "auth_mode" in server_block:
        raw_auth_mode = server_block["auth_mode"]
        if not isinstance(raw_auth_mode, str) or raw_auth_mode not in AUTH_MODES:
            supported_modes = ", ".join(AUTH_MODES)
            raise ConfigError(f"Config file {path}: 'server.auth_mode' must be one of: {supported_modes}.")
        auth_mode = raw_auth_mode

    return ServerSettings(url=url, startup_timeout=startup_timeout, auth_mode=auth_mode)
