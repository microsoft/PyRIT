# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for build_scripts/compose_docs_dist.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "build_scripts" / "compose_docs_dist.py"
BUILD_SCRIPTS = REPO_ROOT / "build_scripts"


@pytest.fixture(scope="module")
def module():
    # compose_docs_dist imports generate_pages_manifest from the same dir.
    sys.path.insert(0, str(BUILD_SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("compose_docs_dist", SCRIPT)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["compose_docs_dist"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        # Leave the sys.path entry so generate_pages_manifest stays importable
        # for the rest of the test session.
        pass


def _make_artifacts(tmp_path: Path, slugs: list[str]) -> Path:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for slug in slugs:
        site = artifacts / f"site-{slug}"
        site.mkdir()
        (site / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
        nested = site / "code" / "scenarios"
        nested.mkdir(parents=True)
        (nested / "index.html").write_text("<html><body>scenarios</body></html>", encoding="utf-8")
    return artifacts


def _make_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "docs-versions.yml"
    cfg.write_text(
        """\
default: "0.13.0"
stable: "0.13.0"
versions:
  - slug: latest
    name: "latest (dev, main)"
    ref: main
  - slug: "0.13.0"
    name: "0.13.0"
    ref: releases/v0.13.0
""",
        encoding="utf-8",
    )
    return cfg


def test_compose_full_pipeline(module, tmp_path):
    artifacts = _make_artifacts(tmp_path, ["latest", "0.13.0"])
    cfg = _make_config(tmp_path)
    dist = tmp_path / "dist"
    rc = module.main(
        [
            "--artifacts-dir",
            str(artifacts),
            "--dist-dir",
            str(dist),
            "--config",
            str(cfg),
            "--base",
            "/PyRIT",
        ]
    )
    assert rc == 0
    # Staged versions
    assert (dist / "latest" / "index.html").is_file()
    assert (dist / "0.13.0" / "index.html").is_file()
    # Per-version manifests
    for slug in ("latest", "0.13.0"):
        pages = json.loads((dist / slug / "pages.json").read_text(encoding="utf-8"))
        assert "" in pages
        assert "code/scenarios/" in pages
    # Top-level manifest
    versions = json.loads((dist / "versions.json").read_text(encoding="utf-8"))
    assert versions["default"] == "0.13.0"
    assert versions["stable"] == "0.13.0"
    assert {v["slug"] for v in versions["versions"]} == {"latest", "0.13.0"}
    # Root + stable redirects
    root = (dist / "index.html").read_text(encoding="utf-8")
    assert 'meta http-equiv="refresh" content="0; url=/PyRIT/0.13.0/"' in root
    stable = (dist / "stable" / "index.html").read_text(encoding="utf-8")
    assert "url=/PyRIT/0.13.0/" in stable
    # 404 page
    not_found = (dist / "404.html").read_text(encoding="utf-8")
    assert "Page not found" in not_found
    assert "/PyRIT/0.13.0/" in not_found


def test_render_redirect_html_quotes(module):
    html = module.render_redirect_html("PyRIT", "/PyRIT/0.13.0/", "PyRIT docs")
    assert "<title>PyRIT</title>" in html
    assert "url=/PyRIT/0.13.0/" in html
    assert '<a href="/PyRIT/0.13.0/">PyRIT docs</a>' in html


def test_render_not_found_includes_default_slug(module):
    html = module.render_not_found_html("/PyRIT", "0.14.0")
    assert "/PyRIT/0.14.0/" in html
    assert "Go to PyRIT 0.14.0 home" in html


def test_render_not_found_includes_auto_redirect_script(module):
    html = module.render_not_found_html("/PyRIT", "0.13.0")
    # The auto-redirect script must be present and target the requesting
    # version's pages.json (not the default version's).
    assert "pages.json" in html
    assert "findClosestPage" in html
    assert "window.location.replace" in html
    # docs_base must be wired into the script body.
    assert 'var docsBase = "/PyRIT"' in html
    # The closest-page algorithm marker must have been substituted with
    # closest_page.js contents -- no marker text in the rendered page.
    assert module.CLOSEST_PAGE_MARKER not in html
    assert "function findClosestPage" in html
    assert "function commonSegmentPrefix" in html


def test_compose_strips_trailing_slash_in_base(module, tmp_path):
    artifacts = _make_artifacts(tmp_path, ["0.13.0"])
    cfg = _make_config(tmp_path)
    dist = tmp_path / "dist"
    rc = module.main(
        [
            "--artifacts-dir",
            str(artifacts),
            "--dist-dir",
            str(dist),
            "--config",
            str(cfg),
            "--base",
            "/PyRIT/",
        ]
    )
    assert rc == 0
    root = (dist / "index.html").read_text(encoding="utf-8")
    # No double slash even though base had a trailing one
    assert "url=/PyRIT/0.13.0/" in root
    assert "//PyRIT" not in root


def test_compose_overwrites_existing_version_dir(module, tmp_path):
    artifacts = _make_artifacts(tmp_path, ["0.13.0"])
    cfg = _make_config(tmp_path)
    dist = tmp_path / "dist"
    # Pre-create a dir with stale content
    (dist / "0.13.0").mkdir(parents=True)
    (dist / "0.13.0" / "stale.txt").write_text("old", encoding="utf-8")
    rc = module.main(
        [
            "--artifacts-dir",
            str(artifacts),
            "--dist-dir",
            str(dist),
            "--config",
            str(cfg),
            "--base",
            "/PyRIT",
        ]
    )
    assert rc == 0
    assert (dist / "0.13.0" / "index.html").is_file()
    assert not (dist / "0.13.0" / "stale.txt").exists()


def test_compose_errors_on_missing_artifacts_dir(module, tmp_path, capsys):
    cfg = _make_config(tmp_path)
    dist = tmp_path / "dist"
    rc = module.main(
        [
            "--artifacts-dir",
            str(tmp_path / "missing"),
            "--dist-dir",
            str(dist),
            "--config",
            str(cfg),
            "--base",
            "/PyRIT",
        ]
    )
    assert rc == 1


def test_compose_errors_on_missing_config(module, tmp_path, capsys):
    artifacts = _make_artifacts(tmp_path, ["0.13.0"])
    dist = tmp_path / "dist"
    rc = module.main(
        [
            "--artifacts-dir",
            str(artifacts),
            "--dist-dir",
            str(dist),
            "--config",
            str(tmp_path / "missing.yml"),
            "--base",
            "/PyRIT",
        ]
    )
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_compose_errors_when_no_site_artifacts(module, tmp_path, capsys):
    cfg = _make_config(tmp_path)
    empty = tmp_path / "empty-artifacts"
    empty.mkdir()
    rc = module.main(
        [
            "--artifacts-dir",
            str(empty),
            "--dist-dir",
            str(tmp_path / "dist"),
            "--config",
            str(cfg),
            "--base",
            "/PyRIT",
        ]
    )
    assert rc == 1
    assert "no site-*" in capsys.readouterr().err
