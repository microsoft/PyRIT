# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Stamp source builds and verify provenance carried by Git-free distributions."""

import argparse
import json
import os
import re
import runpy
import subprocess
import warnings
from collections.abc import Callable
from pathlib import Path
from tempfile import mkstemp
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
is_valid_compatibility_id: Callable[[object], bool] = runpy.run_path(str(ROOT / "pyrit" / "_compatibility.py"))[
    "is_valid_compatibility_id"
]


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE).strip()


def _version(root: Path) -> str:
    version = runpy.run_path(str(root / "pyrit" / "_version.py"))["__version__"]
    if not isinstance(version, str):
        raise ValueError("Python package version must be a string")
    project_file = root / "pyproject.toml"
    if project_file.is_file():
        project_version = re.search(r'^version\s*=\s*"([^"]+)"', project_file.read_text(encoding="utf-8"), re.MULTILINE)
        if project_version is None or project_version[1] != version:
            raise ValueError("Python package and project versions differ")
    return version


def read_stamp(root: Path) -> dict[str, Any]:
    """Read and validate a stamp without importing the installed package.

    Returns:
        dict[str, Any]: Validated provenance.

    Raises:
        ValueError: If provenance is absent or malformed.
    """
    try:
        stamp = json.loads((root / "pyrit" / "_compatibility.json").read_text(encoding="utf-8"))
        version = _version(root)
        if (
            not is_valid_compatibility_id(stamp["compatibility_id"])
            or stamp["version"] != version
            or not isinstance(stamp["commit"], str)
            or stamp["compatibility_id"] != f"{version}+g{stamp['commit']}"
            or not isinstance(stamp["dirty"], bool)
        ):
            raise ValueError("Invalid provenance")
        return stamp
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Missing or malformed PyRIT build provenance") from exc


def _write_stamp(*, root: Path, stamp: dict[str, Any]) -> None:
    """Replace provenance atomically so readers only observe complete stamps."""
    contents = json.dumps(stamp, indent=2) + "\n"
    stamp_path = root / "pyrit" / "_compatibility.json"
    descriptor, temporary_name = mkstemp(dir=stamp_path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(contents)
        temporary_path.chmod(0o644)
        temporary_path.replace(stamp_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def stamp_source(root: Path = ROOT, *, development: bool = False) -> dict[str, Any]:
    """Stamp a checkout or explicitly attributed Docker source tree.

    Args:
        root: Source root.
        development: Allow dirty local builds with a warning, never for publication.

    Returns:
        dict[str, Any]: Source provenance.

    Raises:
        ValueError: If source provenance is missing, inconsistent, or dirty for publication.
    """
    supplied_commit = os.environ.get("PYRIT_SOURCE_COMMIT")
    if (root / ".git").exists():
        commit = _git(root, "rev-parse", "HEAD")
        dirty = bool(_git(root, "status", "--porcelain"))
        if supplied_commit and supplied_commit != commit:
            raise ValueError("Supplied source commit does not match checkout HEAD")
    elif supplied_commit:
        commit = supplied_commit
        modified = os.environ.get("PYRIT_SOURCE_DIRTY")
        if modified not in {"true", "false"}:
            raise ValueError("Source builds without Git require PYRIT_SOURCE_DIRTY=true or false")
        dirty = modified == "true"
    else:
        if (root / "frontend/package.json").is_file():
            raise ValueError("Source builds without Git require explicit PYRIT_SOURCE_COMMIT provenance")
        stamp = read_stamp(root)
        if stamp["dirty"] and not development:
            raise ValueError("Refusing to publish a dirty artifact")
        return stamp
    if dirty:
        if not development:
            raise ValueError("Refusing to publish a dirty source tree")
        warnings.warn("Local edits do not change PyRIT's compatibility identity.", stacklevel=2)
    version = _version(root)
    identity = f"{version}+g{commit}"
    if not is_valid_compatibility_id(identity):
        raise ValueError(
            "Source provenance requires a normalized package version and full lowercase 40-character commit"
        )
    stamp = {"version": version, "commit": commit, "dirty": dirty, "compatibility_id": identity}
    _write_stamp(root=root, stamp=stamp)
    return stamp


def verify_frontend(root: Path, stamp: dict[str, Any]) -> None:
    """Require a frontend entry point and a matching packaged build identity."""
    frontend = root / "pyrit" / "backend" / "frontend"
    if not (frontend / "index.html").is_file():
        raise ValueError("Frontend is missing index.html")
    metadata = json.loads((frontend / "compatibility.json").read_text(encoding="utf-8"))
    if metadata != {"compatibility_id": stamp["compatibility_id"]}:
        raise ValueError("Frontend and Python compatibility identities differ")


def verify_distribution(root: Path = ROOT) -> None:
    """Require clean provenance and a matching frontend identity for Git-free builds."""
    stamp = read_stamp(root)
    if stamp["dirty"]:
        raise ValueError("Refusing to publish a dirty artifact")
    verify_frontend(root, stamp)


def main() -> None:
    """Prepare a local stamp or print it for Vite."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", action="store_true")
    args = parser.parse_args()
    print(stamp_source(development=args.development)["compatibility_id"])


if __name__ == "__main__":
    main()
