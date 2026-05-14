# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Lightweight config reader for the PyRIT CLI thin client.

Reads only the ``server.url`` field from ``~/.pyrit/.pyrit_conf`` (and an
optional overlay file) using ``yaml.safe_load``.  No heavy pyrit imports.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

# Mirror the default path from pyrit.common.path without importing it.
_DEFAULT_CONFIG_DIR = Path.home() / ".pyrit"
_DEFAULT_CONFIG_FILE = _DEFAULT_CONFIG_DIR / ".pyrit_conf"

DEFAULT_SERVER_URL = "http://localhost:8000"


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


def _extract_server_url(*, path: Path, yaml_module: Any) -> str | None:
    """
    Extract ``server.url`` from a single YAML file.

    Args:
        path (Path): YAML config file path.
        yaml_module (Any): The imported ``yaml`` module (passed to avoid
            top-level import).

    Returns:
        str | None: The URL string, or ``None`` if absent/malformed.
    """
    try:
        with open(path) as fh:
            data = yaml_module.safe_load(fh)
        if isinstance(data, dict):
            server_block = data.get("server")
            if isinstance(server_block, dict):
                raw_url = server_block.get("url")
                if isinstance(raw_url, str) and raw_url.strip():
                    return raw_url.strip()
    except Exception:
        _logger.debug("Failed to read server URL from %s", path, exc_info=True)
    return None
