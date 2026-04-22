# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from build_scripts.prepare_package import build_frontend, copy_frontend_to_package


class TestBuildFrontend:
    def test_returns_false_when_npm_not_found(self, tmp_path: Path) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = build_frontend(tmp_path)
        assert result is False

    def test_returns_false_when_package_json_missing(self, tmp_path: Path) -> None:
        mock_run = MagicMock()
        mock_run.return_value.stdout = "10.0.0\n"
        with patch("subprocess.run", mock_run):
            result = build_frontend(tmp_path)
        assert result is False

    def test_returns_false_when_npm_install_fails(self, tmp_path: Path) -> None:
        import subprocess
        (tmp_path / "package.json").write_text("{}")
        responses = [
            MagicMock(stdout="10.0.0\n"),
            subprocess.CalledProcessError(1, "npm install", output="error"),
        ]
        with patch("subprocess.run", side_effect=responses):
            result = build_frontend(tmp_path)
        assert result is False

    def test_returns_false_when_npm_build_fails(self, tmp_path: Path) -> None:
        import subprocess
        (tmp_path / "package.json").write_text("{}")
        responses = [
            MagicMock(stdout="10.0.0\n"),
            MagicMock(),
            subprocess.CalledProcessError(1, "npm run build", output="error"),
        ]
        with patch("subprocess.run", side_effect=responses):
            result = build_frontend(tmp_path)
        assert result is False

    def test_returns_true_when_build_succeeds(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        with patch("subprocess.run", return_value=MagicMock(stdout="10.0.0\n")):
            result = build_frontend(tmp_path)
        assert result is True


class TestCopyFrontendToPackage(object):
    def test_returns_false_when_dist_missing(self, tmp_path: Path) -> None:
        result = copy_frontend_to_package(tmp_path / "dist", tmp_path / "out")
        assert result is False

    def test_returns_false_when_index_html_missing(self, tmp_path: Path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "main.js").write_text("console.log('hi')")
        out = tmp_path / "out"
        result = copy_frontend_to_package(dist, out)
        assert result is False

    def test_returns_true_when_copy_succeeds(self, tmp_path: Path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")
        out = tmp_path / "out"
        result = copy_frontend_to_package(dist, out)
        assert result is True
        assert (out / "index.html").exists()

    def test_removes_existing_output_dir(self, tmp_path: Path) -> None:
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")
        out = tmp_path / "out"
        out.mkdir()
        (out / "old_file.txt").write_text("old")
        copy_frontend_to_package(dist, out)
        assert not (out / "old_file.txt").exists()
        assert (out / "index.html").exists()
