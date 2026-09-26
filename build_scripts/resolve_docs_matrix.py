# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Resolve the matrix and metadata outputs for the docs build workflow.

Reads ``.github/docs-versions.yml`` and writes GitHub Actions step outputs
(``matrix``, ``default``, ``stable``, ``versions_json``, ``compose``) to ``$GITHUB_OUTPUT``,
or to stdout when ``--github-output`` is not provided (for local testing).
PRs validate the tested merge commit under ``latest``. Only composition-input
changes also build the release versions; non-PR runs retain the full matrix.

Usage:
    python -m build_scripts.resolve_docs_matrix \\
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
import re
import subprocess
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


def _requires_composition(changed_files: list[str]) -> bool:
    composition_inputs = {
        ".github/docs-versions.yml",
        ".github/workflows/docs.yml",
        "build_scripts/resolve_docs_matrix.py",
        "build_scripts/compose_docs_dist.py",
        "build_scripts/generate_pages_manifest.py",
        "build_scripts/inject_version_picker.py",
    }
    return any(
        path in composition_inputs or path.startswith("build_scripts/version_picker_assets/") for path in changed_files
    )


def _changed_files(sha: str) -> list[str]:
    """Compare against the tested merge's parent, not a moving base branch."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{sha}^1", sha, "--"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.split("\0")[:-1]


def build_outputs(
    *,
    cfg: dict[str, Any],
    event_name: str = "",
    ref: str = "",
    sha: str = "",
    changed_files: list[str] | None = None,
) -> dict[str, str]:
    """Return matrix selection and site metadata as single-line Actions outputs."""
    if event_name and not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("workflow builds require the full 40-character tested commit SHA")
    if event_name and not ref:
        raise ValueError("workflow builds require the triggering GITHUB_REF")
    versions = cfg["versions"]
    entries = [{"slug": v["slug"], "ref": v["ref"]} for v in versions]
    compose = True
    if event_name == "pull_request":
        if changed_files is None:
            raise ValueError("PR matrix selection requires the tested merge's changed files")
        if sum(entry["slug"] == "latest" for entry in entries) != 1:
            raise ValueError("PR validation requires exactly one 'latest' version")
        compose = _requires_composition(changed_files)
        entries = [
            {"slug": entry["slug"], "ref": sha if entry["slug"] == "latest" else entry["ref"]}
            for entry in entries
            if compose or entry["slug"] == "latest"
        ]
    elif event_name:
        tested_entries = [entry for entry in entries if entry["ref"] in (ref, ref.removeprefix("refs/heads/"))]
        if not tested_entries and ref != "refs/heads/main":
            # Newly cut releases are added to the version list on main later.
            tested_entries = [entry for entry in entries if entry["slug"] == "latest"]
            if len(tested_entries) != 1:
                raise ValueError("unlisted-ref validation requires exactly one 'latest' version")
        for entry in tested_entries:
            entry["ref"] = sha
    matrix = {"include": entries}
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
        "compose": str(compose).lower(),
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
    parser.add_argument("--event-name", choices=["pull_request", "push", "workflow_dispatch"])
    parser.add_argument("--ref", default="", help="The workflow's GITHUB_REF.")
    parser.add_argument("--sha", default="", help="The immutable tested GITHUB_SHA, not a branch or PR head ref.")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config.resolve())
        outputs = build_outputs(
            cfg=cfg,
            event_name=args.event_name or "",
            ref=args.ref,
            sha=args.sha,
            changed_files=_changed_files(args.sha) if args.event_name == "pull_request" else None,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"error: cannot compare the tested PR merge with its first parent: {e.stderr.strip()}", file=sys.stderr)
        return 1

    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as f:
            write_outputs(f, outputs)
    else:
        write_outputs(sys.stdout, outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
