# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for build_scripts/resolve_docs_matrix.py."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "build_scripts" / "resolve_docs_matrix.py"
TESTED_SHA = "a" * 40


@pytest.fixture(scope="module")
def module():
    spec = importlib.util.spec_from_file_location("resolve_docs_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["resolve_docs_matrix"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "docs-versions.yml"
    p.write_text(content, encoding="utf-8")
    return p


_VALID_YAML = """\
default: "0.13.0"
stable: "0.13.0"
versions:
  - slug: latest
    name: "latest (dev, main)"
    ref: main
  - slug: "0.13.0"
    name: "0.13.0"
    ref: releases/v0.13.0
  - slug: "0.12.1"
    name: "0.12.1"
    ref: releases/v0.12.1
"""


def test_build_outputs_shape(module, tmp_path):
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    outputs = module.build_outputs(cfg=cfg)
    assert set(outputs.keys()) == {"matrix", "default", "stable", "versions_json", "compose"}
    assert outputs["compose"] == "true"
    assert outputs["default"] == "0.13.0"
    assert outputs["stable"] == "0.13.0"
    matrix = json.loads(outputs["matrix"])
    assert matrix == {
        "include": [
            {"slug": "latest", "ref": "main"},
            {"slug": "0.13.0", "ref": "releases/v0.13.0"},
            {"slug": "0.12.1", "ref": "releases/v0.12.1"},
        ]
    }
    payload = json.loads(outputs["versions_json"])
    assert payload["default"] == "0.13.0"
    assert payload["stable"] == "0.13.0"
    assert len(payload["versions"]) == 3


def test_outputs_are_single_line(module, tmp_path):
    """GH Actions step outputs are key=value lines; no embedded newlines."""
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    outputs = module.build_outputs(cfg=cfg)
    for key, value in outputs.items():
        assert "\n" not in value, f"output {key} contains a newline; would break $GITHUB_OUTPUT"


def test_write_outputs_format(module, tmp_path):
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    outputs = module.build_outputs(cfg=cfg)
    sink = io.StringIO()
    module.write_outputs(sink, outputs)
    lines = sink.getvalue().splitlines()
    keys_seen = [line.split("=", 1)[0] for line in lines]
    assert keys_seen == ["matrix", "default", "stable", "versions_json", "compose"]


def test_write_outputs_rejects_multiline_value(module):
    sink = io.StringIO()
    with pytest.raises(ValueError, match="contains a newline"):
        module.write_outputs(sink, {"matrix": "line1\nline2"})


def test_load_config_rejects_missing_default(module, tmp_path):
    bad = """
stable: "0.13.0"
versions:
  - slug: latest
    name: latest
    ref: main
"""
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="missing required key 'default'"):
        module.load_config(p)


def test_load_config_rejects_default_not_in_versions(module, tmp_path):
    bad = """
default: "9.9.9"
stable: "0.13.0"
versions:
  - slug: "0.13.0"
    name: "0.13.0"
    ref: releases/v0.13.0
"""
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="'default' value '9.9.9' is not among"):
        module.load_config(p)


def test_load_config_rejects_stable_not_in_versions(module, tmp_path):
    bad = """
default: "0.13.0"
stable: "9.9.9"
versions:
  - slug: "0.13.0"
    name: "0.13.0"
    ref: releases/v0.13.0
"""
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="'stable' value '9.9.9' is not among"):
        module.load_config(p)


def test_load_config_rejects_version_missing_fields(module, tmp_path):
    bad = """
default: "0.13.0"
stable: "0.13.0"
versions:
  - slug: "0.13.0"
    name: "0.13.0"
"""  # missing ref
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="missing required key 'ref'"):
        module.load_config(p)


def test_load_config_rejects_empty_versions(module, tmp_path):
    bad = """
default: "0.13.0"
stable: "0.13.0"
versions: []
"""
    p = _write_yaml(tmp_path, bad)
    with pytest.raises(ValueError, match="non-empty list"):
        module.load_config(p)


def test_load_config_missing_file(module, tmp_path):
    with pytest.raises(FileNotFoundError):
        module.load_config(tmp_path / "does-not-exist.yml")


def test_main_writes_to_github_output(module, tmp_path, capsys):
    config = _write_yaml(tmp_path, _VALID_YAML)
    out_file = tmp_path / "gha-output"
    rc = module.main(["--config", str(config), "--github-output", str(out_file)])
    assert rc == 0
    content = out_file.read_text(encoding="utf-8")
    assert "default=0.13.0\n" in content
    assert "stable=0.13.0\n" in content
    assert "matrix=" in content
    assert "versions_json=" in content


def test_main_writes_to_stdout_when_no_github_output(module, tmp_path, capsys):
    config = _write_yaml(tmp_path, _VALID_YAML)
    rc = module.main(["--config", str(config)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "default=0.13.0" in out
    assert "stable=0.13.0" in out


def test_main_error_on_missing_config(module, tmp_path, capsys):
    rc = module.main(["--config", str(tmp_path / "missing.yml")])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    "changed_path",
    [
        "doc/contributing/7_notebooks.md",
        "pyrit/models/message.py",
        "pyproject.toml",
        "uv.lock",
        "build_scripts/pydoc2json.py",
        "build_scripts/gen_api_md.py",
        "tests/unit/build_scripts/test_resolve_docs_matrix.py",
    ],
)
def test_ordinary_pr_only_builds_tested_merge(*, module: ModuleType, tmp_path: Path, changed_path: str) -> None:
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    original = json.dumps(cfg)
    outputs = module.build_outputs(
        cfg=cfg,
        event_name="pull_request",
        ref="refs/pull/42/merge",
        sha=TESTED_SHA,
        changed_files=[changed_path],
    )

    assert json.loads(outputs["matrix"]) == {"include": [{"slug": "latest", "ref": TESTED_SHA}]}
    assert outputs["compose"] == "false"
    assert json.loads(outputs["versions_json"]) == cfg
    assert json.dumps(cfg) == original


@pytest.mark.parametrize(
    "changed_path",
    [
        ".github/docs-versions.yml",
        ".github/workflows/docs.yml",
        "build_scripts/resolve_docs_matrix.py",
        "build_scripts/compose_docs_dist.py",
        "build_scripts/generate_pages_manifest.py",
        "build_scripts/inject_version_picker.py",
        "build_scripts/version_picker_assets/picker.js",
        "build_scripts/version_picker_assets/closest_page.js",
    ],
)
def test_composition_pr_keeps_all_releases_and_tested_merge(
    *, module: ModuleType, tmp_path: Path, changed_path: str
) -> None:
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    outputs = module.build_outputs(
        cfg=cfg,
        event_name="pull_request",
        ref="refs/pull/42/merge",
        sha=TESTED_SHA,
        changed_files=["doc/index.md", changed_path],
    )

    assert json.loads(outputs["matrix"]) == {
        "include": [
            {"slug": "latest", "ref": TESTED_SHA},
            {"slug": "0.13.0", "ref": "releases/v0.13.0"},
            {"slug": "0.12.1", "ref": "releases/v0.12.1"},
        ]
    }
    assert outputs["compose"] == "true"
    assert json.loads(outputs["versions_json"]) == cfg


@pytest.mark.parametrize("event_name", ["push", "workflow_dispatch"])
@pytest.mark.parametrize("branch", ["main", "releases/v0.13.0"])
def test_non_pr_keeps_full_matrix_and_pins_triggering_branch(
    *, module: ModuleType, tmp_path: Path, event_name: str, branch: str
) -> None:
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    outputs = module.build_outputs(cfg=cfg, event_name=event_name, ref=f"refs/heads/{branch}", sha=TESTED_SHA)

    assert json.loads(outputs["matrix"]) == {
        "include": [{"slug": v["slug"], "ref": TESTED_SHA if v["ref"] == branch else v["ref"]} for v in cfg["versions"]]
    }
    assert outputs["compose"] == "true"
    assert json.loads(outputs["versions_json"]) == cfg


@pytest.mark.parametrize(
    ("event_name", "ref"),
    [
        ("push", "refs/heads/releases/v9.9.9"),
        ("workflow_dispatch", "refs/heads/releases/v9.9.9"),
        ("workflow_dispatch", "refs/heads/docs-preview"),
        ("workflow_dispatch", "refs/tags/v9.9.9"),
    ],
)
def test_unlisted_non_main_ref_validates_its_commit_without_dropping_releases(
    *, module: ModuleType, tmp_path: Path, event_name: str, ref: str
) -> None:
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    outputs = module.build_outputs(cfg=cfg, event_name=event_name, ref=ref, sha=TESTED_SHA)
    assert json.loads(outputs["matrix"]) == {
        "include": [
            {"slug": v["slug"], "ref": TESTED_SHA if v["slug"] == "latest" else v["ref"]} for v in cfg["versions"]
        ]
    }
    assert outputs["compose"] == "true"
    assert json.loads(outputs["versions_json"]) == cfg


def test_unlisted_ref_substitution_does_not_change_main_publication(*, module: ModuleType, tmp_path: Path) -> None:
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    cfg["versions"][0]["ref"] = "b" * 40
    outputs = module.build_outputs(cfg=cfg, event_name="push", ref="refs/heads/main", sha=TESTED_SHA)
    assert json.loads(outputs["matrix"]) == {"include": [{"slug": v["slug"], "ref": v["ref"]} for v in cfg["versions"]]}


def test_workflow_requires_triggering_ref(*, module: ModuleType, tmp_path: Path) -> None:
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    with pytest.raises(ValueError, match="require the triggering GITHUB_REF"):
        module.build_outputs(cfg=cfg, event_name="push", sha=TESTED_SHA)


@pytest.mark.parametrize("sha", ["", "main", "refs/pull/42/merge", "abc123", "z" * 40])
def test_workflow_matrix_rejects_mutable_or_invalid_sha(*, module: ModuleType, tmp_path: Path, sha: str) -> None:
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    with pytest.raises(ValueError, match="40-character tested commit SHA"):
        module.build_outputs(cfg=cfg, event_name="pull_request", ref="refs/pull/42/merge", sha=sha, changed_files=[])


def test_pr_requires_changed_files(*, module: ModuleType, tmp_path: Path) -> None:
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    with pytest.raises(ValueError, match="requires the tested merge's changed files"):
        module.build_outputs(cfg=cfg, event_name="pull_request", ref="refs/pull/42/merge", sha=TESTED_SHA)


@pytest.mark.parametrize("latest_count", [0, 2])
@pytest.mark.parametrize("event_name", ["pull_request", "push"])
def test_validation_requires_one_latest_version(
    *, module: ModuleType, tmp_path: Path, latest_count: int, event_name: str
) -> None:
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    latest = cfg["versions"].pop(0)
    cfg["versions"].extend([latest] * latest_count)
    with pytest.raises(ValueError, match="exactly one 'latest' version"):
        module.build_outputs(
            cfg=cfg,
            event_name=event_name,
            ref="refs/pull/42/merge" if event_name == "pull_request" else "refs/heads/releases/v9.9.9",
            sha=TESTED_SHA,
            changed_files=[],
        )


def test_main_pr_passes_immutable_sha_to_diff(
    *, module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _write_yaml(tmp_path, _VALID_YAML)
    with patch.object(module, "_changed_files", return_value=["doc/index.md"]) as diff:
        rc = module.main(
            [
                "--config",
                str(config),
                "--event-name",
                "pull_request",
                "--ref",
                "refs/pull/42/merge",
                "--sha",
                TESTED_SHA,
            ]
        )
    assert rc == 0
    diff.assert_called_once_with(TESTED_SHA)
    outputs = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert json.loads(outputs["matrix"]) == {"include": [{"slug": "latest", "ref": TESTED_SHA}]}
    assert outputs["compose"] == "false"
