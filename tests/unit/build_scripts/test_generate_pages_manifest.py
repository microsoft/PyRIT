# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for build_scripts/generate_pages_manifest.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "build_scripts" / "generate_pages_manifest.py"


@pytest.fixture(scope="module")
def manifest_module():
    spec = importlib.util.spec_from_file_location("generate_pages_manifest", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_pages_manifest"] = module
    spec.loader.exec_module(module)
    return module


def _make_site(tmp_path: Path, page_paths: list[str]) -> Path:
    site = tmp_path / "site"
    site.mkdir()
    for p in page_paths:
        if p == "":
            (site / "index.html").write_text("<html></html>", encoding="utf-8")
        else:
            d = site / p
            d.mkdir(parents=True, exist_ok=True)
            (d / "index.html").write_text("<html></html>", encoding="utf-8")
    return site


def test_collects_root_and_nested(manifest_module, tmp_path):
    site = _make_site(tmp_path, ["", "code", "code/scenarios", "code/scenarios/scenarios"])
    out = tmp_path / "pages.json"
    rc = manifest_module.main(["--site-dir", str(site), "--output", str(out)])
    assert rc == 0
    pages = json.loads(out.read_text(encoding="utf-8"))
    assert "" in pages
    assert "code/" in pages
    assert "code/scenarios/" in pages
    assert "code/scenarios/scenarios/" in pages


def test_sorts_output(manifest_module, tmp_path):
    site = _make_site(tmp_path, ["zebra", "alpha", "code/zebra", "code/alpha"])
    out = tmp_path / "pages.json"
    manifest_module.main(["--site-dir", str(site), "--output", str(out)])
    pages = json.loads(out.read_text(encoding="utf-8"))
    assert pages == sorted(pages)


def test_excludes_dirs_without_index_html(manifest_module, tmp_path):
    site = _make_site(tmp_path, ["", "code/scenarios/scenarios"])
    # Add a sibling dir that has subpages but no index.html of its own
    (site / "code" / "datasets" / "loading").mkdir(parents=True)
    (site / "code" / "datasets" / "loading" / "index.html").write_text("<html></html>", encoding="utf-8")
    out = tmp_path / "pages.json"
    manifest_module.main(["--site-dir", str(site), "--output", str(out)])
    pages = json.loads(out.read_text(encoding="utf-8"))
    assert "code/datasets/loading/" in pages
    assert "code/datasets/" not in pages  # no index.html at this level
    assert "code/" not in pages  # no index.html at this level


def test_missing_site_dir_returns_error(manifest_module, tmp_path, capsys):
    out = tmp_path / "pages.json"
    rc = manifest_module.main(["--site-dir", str(tmp_path / "missing"), "--output", str(out)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "does not exist" in err


def test_empty_site_yields_empty_manifest(manifest_module, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    out = tmp_path / "pages.json"
    rc = manifest_module.main(["--site-dir", str(site), "--output", str(out)])
    assert rc == 0
    assert json.loads(out.read_text(encoding="utf-8")) == []
