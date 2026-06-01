# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Tests for build_scripts/inject_version_picker.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "build_scripts" / "inject_version_picker.py"


@pytest.fixture(scope="module")
def injector_module():
    spec = importlib.util.spec_from_file_location("inject_version_picker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["inject_version_picker"] = module
    spec.loader.exec_module(module)
    return module


def _build_site(tmp_path: Path, html: str = "<html><head><title>x</title></head><body>hi</body></html>") -> Path:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(html, encoding="utf-8")
    (site / "subdir").mkdir()
    (site / "subdir" / "page.html").write_text(html, encoding="utf-8")
    return site


def test_injects_meta_and_inline_script(injector_module, tmp_path):
    site = _build_site(tmp_path)
    rc = injector_module.main(["--site-dir", str(site), "--base", "/PyRIT/0.13.0"])
    assert rc == 0
    out = (site / "index.html").read_text(encoding="utf-8")
    assert '<meta name="pyrit-docs-base" content="/PyRIT/0.13.0">' in out
    assert "<script>" in out
    # Picker JS body must be inlined; we sanity-check by looking for the
    # mount-class constant and the styles bundle.
    assert "pyrit-version-picker" in out
    assert "position: fixed" in out
    # The closest-page algorithm must have been substituted in, with no
    # marker text leaking through to the rendered page.
    assert "findClosestPage" in out
    assert injector_module.CLOSEST_PAGE_MARKER not in out
    assert injector_module.INJECT_MARKER in out


def test_no_asset_directory_is_created(injector_module, tmp_path):
    site = _build_site(tmp_path)
    injector_module.main(["--site-dir", str(site), "--base", "/PyRIT/latest"])
    # All picker assets are inlined; no _pyrit/ dir should be written.
    assert not (site / "_pyrit").exists()


def test_idempotent(injector_module, tmp_path):
    site = _build_site(tmp_path)
    injector_module.main(["--site-dir", str(site), "--base", "/PyRIT/0.13.0"])
    first = (site / "index.html").read_text(encoding="utf-8")
    injector_module.main(["--site-dir", str(site), "--base", "/PyRIT/0.13.0"])
    second = (site / "index.html").read_text(encoding="utf-8")
    assert first == second
    assert second.count(injector_module.INJECT_MARKER) == 1


def test_walks_subdirectories(injector_module, tmp_path):
    site = _build_site(tmp_path)
    injector_module.main(["--site-dir", str(site), "--base", "/PyRIT"])
    nested = (site / "subdir" / "page.html").read_text(encoding="utf-8")
    assert '<meta name="pyrit-docs-base" content="/PyRIT">' in nested
    assert "pyrit-version-picker" in nested


def test_trailing_slash_in_base_is_stripped(injector_module, tmp_path):
    site = _build_site(tmp_path)
    injector_module.main(["--site-dir", str(site), "--base", "/PyRIT/0.13.0/"])
    out = (site / "index.html").read_text(encoding="utf-8")
    assert '<meta name="pyrit-docs-base" content="/PyRIT/0.13.0">' in out


def test_missing_site_dir_returns_error(injector_module, tmp_path, capsys):
    rc = injector_module.main(["--site-dir", str(tmp_path / "missing"), "--base", "/PyRIT"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "does not exist" in err


def test_handles_html_without_head(injector_module, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "fragment.html").write_text("<p>just a fragment</p>", encoding="utf-8")
    rc = injector_module.main(["--site-dir", str(site), "--base", "/PyRIT"])
    assert rc == 0
    out = (site / "fragment.html").read_text(encoding="utf-8")
    assert '<meta name="pyrit-docs-base" content="/PyRIT">' in out
    assert "pyrit-version-picker" in out
