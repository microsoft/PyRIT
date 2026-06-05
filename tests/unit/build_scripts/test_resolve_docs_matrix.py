# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for build_scripts/resolve_docs_matrix.py."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "build_scripts" / "resolve_docs_matrix.py"


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
    outputs = module.build_outputs(cfg)
    assert set(outputs.keys()) == {"matrix", "default", "stable", "versions_json"}
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
    outputs = module.build_outputs(cfg)
    for key, value in outputs.items():
        assert "\n" not in value, f"output {key} contains a newline; would break $GITHUB_OUTPUT"


def test_write_outputs_format(module, tmp_path):
    cfg = module.load_config(_write_yaml(tmp_path, _VALID_YAML))
    outputs = module.build_outputs(cfg)
    sink = io.StringIO()
    module.write_outputs(sink, outputs)
    lines = sink.getvalue().splitlines()
    keys_seen = [line.split("=", 1)[0] for line in lines]
    assert keys_seen == ["matrix", "default", "stable", "versions_json"]


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
