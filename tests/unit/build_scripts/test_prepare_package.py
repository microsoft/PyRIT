# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from build_scripts.prepare_package import build_frontend, copy_frontend_to_package


def test_build_frontend_returns_false_when_npm_not_found(tmp_path: Path) -> None:
    with patch("build_scripts.prepare_package.shutil.which", return_value=None):
        result = build_frontend(tmp_path)
    assert result is False


def test_build_frontend_returns_false_when_package_json_missing(tmp_path: Path) -> None:
    with patch("build_scripts.prepare_package.shutil.which", return_value="/usr/bin/npm"):
        with patch("build_scripts.prepare_package.subprocess.run", return_value=MagicMock(stdout="10.0.0\n")):
            result = build_frontend(tmp_path)
    assert result is False


def test_build_frontend_returns_false_when_npm_install_fails(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    responses = [
        MagicMock(stdout="10.0.0\n"),
        subprocess.CalledProcessError(1, "npm install", output="error"),
    ]
    with patch("build_scripts.prepare_package.shutil.which", return_value="/usr/bin/npm"):
        with patch("build_scripts.prepare_package.subprocess.run", side_effect=responses):
            result = build_frontend(tmp_path)
    assert result is False


def test_build_frontend_returns_false_when_npm_build_fails(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    responses = [
        MagicMock(stdout="10.0.0\n"),
        MagicMock(),
        subprocess.CalledProcessError(1, "npm run build", output="error"),
    ]
    with patch("build_scripts.prepare_package.shutil.which", return_value="/usr/bin/npm"):
        with patch("build_scripts.prepare_package.subprocess.run", side_effect=responses):
            result = build_frontend(tmp_path)
    assert result is False


def test_build_frontend_returns_true_when_build_succeeds(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    responses = [
        MagicMock(stdout="10.0.0\n"),  # npm --version
        MagicMock(),  # npm install
        MagicMock(),  # npm run build
    ]
    with patch("build_scripts.prepare_package.shutil.which", return_value="/usr/bin/npm"):
        with patch("build_scripts.prepare_package.subprocess.run", side_effect=responses) as mock_run:
            result = build_frontend(tmp_path)
    assert result is True
    assert mock_run.call_count == 3


def test_copy_frontend_returns_false_when_dist_missing(tmp_path: Path) -> None:
    result = copy_frontend_to_package(tmp_path / "dist", tmp_path / "out")
    assert result is False


def test_copy_frontend_returns_false_when_index_html_missing(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "main.js").write_text("console.log('hi')")
    result = copy_frontend_to_package(dist, tmp_path / "out")
    assert result is False


def test_copy_frontend_returns_true_when_copy_succeeds(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>")
    out = tmp_path / "out"
    result = copy_frontend_to_package(dist, out)
    assert result is True
    assert (out / "index.html").exists()


def test_copy_frontend_removes_existing_output_dir(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>")
    out = tmp_path / "out"
    out.mkdir()
    (out / "old_file.txt").write_text("old")
    copy_frontend_to_package(dist, out)
    assert not (out / "old_file.txt").exists()
    assert (out / "index.html").exists()
