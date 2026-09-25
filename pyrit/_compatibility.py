# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Dependency-free, installed-artifact identity and lockstep protocol constants."""

import json
import re
import warnings
from pathlib import Path
from typing import TypeGuard

COMPATIBILITY_HEADER = "PyRIT-Compatibility-ID"
INVALID_COMPATIBILITY_TYPE = "urn:pyrit:compatibility:invalid"
MISMATCH_COMPATIBILITY_TYPE = "urn:pyrit:compatibility:mismatch"
_IDENTITY_PATTERN = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?\+g[0-9a-f]{40}",
    re.ASCII,
)


def is_valid_compatibility_id(value: object) -> TypeGuard[str]:
    """Return whether a marker contains a package version and full source commit."""
    return isinstance(value, str) and len(value) <= 256 and _IDENTITY_PATTERN.fullmatch(value) is not None


def get_compatibility_id() -> str:
    """
    Read packaged provenance, never Git or a connected server.

    Returns:
        str: The artifact's compatibility identity.

    Raises:
        ValueError: If the stamp is missing, malformed, or for another package version.
    """
    from pyrit._version import __version__

    try:
        stamp = json.loads(Path(__file__).with_name("_compatibility.json").read_text(encoding="utf-8"))
        identity = stamp["compatibility_id"]
        if (
            not is_valid_compatibility_id(identity)
            or stamp["version"] != __version__
            or identity != f"{__version__}+g{stamp['commit']}"
            or not isinstance(stamp["dirty"], bool)
        ):
            raise ValueError("Invalid compatibility stamp")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            "Missing or malformed PyRIT compatibility provenance. Reinstall a coordinated release, "
            "or run 'python -m build_scripts.stamp_compatibility --development' in a source checkout."
        ) from exc
    if stamp["dirty"]:
        warnings.warn("PyRIT contains local edits; compatibility identity does not distinguish them.", stacklevel=2)
    return identity
