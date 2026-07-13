# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Resolve the matrix and metadata outputs for the docs build workflow.

Reads ``.github/docs-versions.yml`` and writes GitHub Actions step outputs
(``matrix``, ``default``, ``stable``, ``versions_json``) to ``$GITHUB_OUTPUT``,
or to stdout when ``--github-output`` is not provided (for local testing).

Usage:
    python build_scripts/resolve_docs_matrix.py \\
        --config .github/docs-versions.yml \\
        --github-output "$GITHUB_OUTPUT"

This replaces a previous heredoc-embedded Python snippet in the workflow file.
Keeping the logic as a real Python module means it can be linted, type-checked,
and unit-tested -- previously a single YAML formatting slip-up (e.g. a
multi-line JSON value) could silently produce a corrupt ``versions.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import IO, Any

import yaml


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        raise FileNotFoundError(f"docs-versions config not found at {config_path}")
    with config_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"{config_path}: top-level YAML must be a mapping, got {type(cfg).__name__}")
    for key in ("default", "stable", "versions"):
        if key not in cfg:
            raise ValueError(f"{config_path}: missing required key '{key}'")
    if not isinstance(cfg["versions"], list) or not cfg["versions"]:
        raise ValueError(f"{config_path}: 'versions' must be a non-empty list")
    for entry in cfg["versions"]:
        for key in ("slug", "name", "ref"):
            if key not in entry:
                raise ValueError(f"{config_path}: version entry {entry!r} missing required key '{key}'")
    slugs = {v["slug"] for v in cfg["versions"]}
    if cfg["default"] not in slugs:
        raise ValueError(
            f"{config_path}: 'default' value {cfg['default']!r} is not among versions slugs {sorted(slugs)}"
        )
    if cfg["stable"] not in slugs:
        raise ValueError(f"{config_path}: 'stable' value {cfg['stable']!r} is not among versions slugs {sorted(slugs)}")
    return cfg


def build_outputs(cfg: dict[str, Any]) -> dict[str, str]:
    """Return the four GitHub Actions step outputs (all values are strings)."""
    versions = cfg["versions"]
    matrix = {"include": [{"slug": v["slug"], "ref": v["ref"]} for v in versions]}
    versions_json = {
        "default": cfg["default"],
        "stable": cfg["stable"],
        "versions": versions,
    }
    # Compact JSON so the output line stays on a single physical line, which is
    # the format GitHub Actions step outputs require.
    return {
        "matrix": json.dumps(matrix, separators=(",", ":")),
        "default": cfg["default"],
        "stable": cfg["stable"],
        "versions_json": json.dumps(versions_json, separators=(",", ":")),
    }


def write_outputs(out: IO[str], outputs: dict[str, str]) -> None:
    for key, value in outputs.items():
        if "\n" in value:
            raise ValueError(
                f"output {key!r} contains a newline; cannot be written as a single-line GH Actions step output"
            )
        out.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(".github/docs-versions.yml"),
        help="Path to docs-versions.yml (default: .github/docs-versions.yml)",
    )
    parser.add_argument(
        "--github-output",
        type=Path,
        help="Path to the $GITHUB_OUTPUT file. If omitted, outputs are written to stdout.",
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config.resolve())
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    outputs = build_outputs(cfg)

    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as f:
            write_outputs(f, outputs)
    else:
        write_outputs(sys.stdout, outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
