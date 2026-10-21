# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Fast, safe YAML loading.

PyRIT parses thousands of YAML documents (seed datasets, scorer and converter
configurations) during normal use. ``yaml.safe_load`` uses PyYAML's pure-Python
parser; the libyaml-backed ``CSafeLoader`` parses the same documents several
times faster. ``CSafeLoader`` is only present when PyYAML was built against
libyaml, so fall back to the pure-Python ``SafeLoader`` when it is missing.

Both loaders implement the identical YAML 1.1 safe schema, so this is a drop-in
replacement for ``yaml.safe_load``.
"""

from typing import IO, Any

import yaml

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:  # pragma: no cover - depends on whether libyaml is available
    from yaml import SafeLoader  # type: ignore[assignment]


def safe_load_yaml(source: str | bytes | IO[str] | IO[bytes]) -> Any:
    """
    Parse a YAML document using the fastest available safe loader.

    Args:
        source: YAML text, bytes, or an open file-like object, matching what
            ``yaml.safe_load`` accepts.

    Returns:
        Any: The parsed YAML content, or ``None`` if the document is empty.

    Raises:
        yaml.YAMLError: If the document is not valid YAML.
    """
    return yaml.load(source, Loader=SafeLoader)
