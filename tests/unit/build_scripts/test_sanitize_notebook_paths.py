# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import os
import tempfile
from pathlib import Path

import pytest

from build_scripts.sanitize_notebook_paths import _strip_user_paths, sanitize_notebook_paths


@pytest.mark.parametrize(
    "url",
    [
        "https://private-vault.vault.azure.net/secrets/private-env",
        "https://private-vault.vault.azure.net/secrets/private-env/0123456789abcdef",
        "https://private-vault.vault.azure.cn/secrets/private-env",
        "https://private-vault.vault.usgovcloudapi.net/secrets/private-env",
        "HTTPS://PRIVATE-VAULT.VAULT.MICROSOFTAZURE.DE/secrets/PRIVATE-ENV/VERSION",
    ],
)
def test_sanitizes_key_vault_secret_urls(url: str) -> None:
    text = f"Loading '{url}'; failed ({url})."
    expected = "https://my-vault.vault.azure.net/secrets/my-pyrit-env"
    assert _strip_user_paths(text) == f"Loading '{expected}'; failed ({expected})."


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/secrets/private-env",
        "https://private-vault.vault.azure.net.example.com/secrets/private-env",
        "https://my-vault.vault.azure.net/secrets/my-pyrit-env",
    ],
)
def test_preserves_non_key_vault_urls_and_placeholder(text: str) -> None:
    assert _strip_user_paths(text) == text


def test_sanitizes_key_vault_urls_only_in_notebook_text_outputs(tmp_path: Path) -> None:
    url = "https://private-vault.vault.azure.net/secrets/private-env/0123456789abcdef"
    expected = "https://my-vault.vault.azure.net/secrets/my-pyrit-env"
    notebook = {
        "metadata": {"url": url},
        "cells": [
            {
                "cell_type": "code",
                "source": [f"secret_url = '{url}'"],
                "outputs": [
                    {"output_type": "stream", "name": "stdout", "text": [f"Loading {url}\n"]},
                    {"output_type": "stream", "name": "stderr", "text": f"Failed {url}"},
                    {"output_type": "error", "ename": "ValueError", "evalue": url, "traceback": [url]},
                    {
                        "output_type": "display_data",
                        "data": {"text/plain": [url], "text/html": f"<p>{url}</p>", "application/json": {"url": url}},
                    },
                ],
            }
        ],
    }
    notebook_path = tmp_path / "example.ipynb"
    notebook_path.write_text(json.dumps(notebook), encoding="utf-8")

    assert sanitize_notebook_paths(str(notebook_path)) is True
    result = json.loads(notebook_path.read_text(encoding="utf-8"))
    assert result["metadata"] == notebook["metadata"]
    assert result["cells"][0]["source"] == notebook["cells"][0]["source"]
    outputs = result["cells"][0]["outputs"]
    assert outputs[0]["text"] == [f"Loading {expected}\n"]
    assert outputs[1]["text"] == f"Failed {expected}"
    assert outputs[2]["evalue"] == expected
    assert outputs[2]["traceback"] == [expected]
    assert outputs[3]["data"] == {
        "text/plain": [expected],
        "text/html": f"<p>{expected}</p>",
        "application/json": {"url": url},
    }
    assert sanitize_notebook_paths(str(notebook_path)) is False


class TestStripUserPaths:
    def test_windows_path(self) -> None:
        assert _strip_user_paths(r"C:\Users\testuser\git\PyRIT\foo.py") == "./git/PyRIT/foo.py"

    def test_windows_double_backslash(self) -> None:
        result = _strip_user_paths("C:\\\\Users\\\\alice\\\\AppData\\\\Local\\\\Temp\\\\file.py")
        assert result == "./AppData/Local/Temp/file.py"

    def test_linux_path(self) -> None:
        assert _strip_user_paths("/home/user1/projects/pyrit/foo.py") == "./projects/pyrit/foo.py"

    def test_macos_path(self) -> None:
        assert _strip_user_paths("/Users/alice/Documents/test.txt") == "./Documents/test.txt"

    def test_no_user_path(self) -> None:
        text = "just a normal string"
        assert _strip_user_paths(text) == text

    def test_drive_letter_d(self) -> None:
        assert _strip_user_paths(r"D:\Users\testuser\hello.py") == "./hello.py"

    def test_lowercase_drive_letter(self) -> None:
        assert _strip_user_paths(r"c:\Users\testuser\project\file.py") == "./project/file.py"

    def test_lowercase_users_segment(self) -> None:
        assert _strip_user_paths(r"C:\users\testuser\project\file.py") == "./project/file.py"

    def test_copilot_worktree_windows(self) -> None:
        assert (
            _strip_user_paths(r"C:\Users\testuser\.copilot\repos\copilot-worktrees\pyrit\my-branch\.venv\Lib\file.py")
            == "./.venv/Lib/file.py"
        )

    def test_copilot_worktree_linux(self) -> None:
        assert (
            _strip_user_paths("/home/testuser/.copilot/repos/copilot-worktrees/pyrit/my-branch/.venv/lib/file.py")
            == "./.venv/lib/file.py"
        )

    def test_copilot_repo_no_worktree(self) -> None:
        assert _strip_user_paths("/home/testuser/.copilot/repos/pyrit/src/foo.py") == "./src/foo.py"

    def test_copilot_path_outside_repos_untouched(self) -> None:
        # Only paths under .copilot/repos/ are collapsed.
        assert _strip_user_paths("/home/testuser/.copilot/config/settings.json") == "./.copilot/config/settings.json"


class TestSanitizeNotebookPaths:
    def _make_notebook(self, outputs: list) -> dict:
        return {
            "cells": [
                {
                    "cell_type": "code",
                    "outputs": outputs,
                }
            ]
        }

    def test_sanitizes_stderr_output(self) -> None:
        nb = self._make_notebook(
            [
                {
                    "output_type": "stream",
                    "name": "stderr",
                    "text": [r"C:\Users\test\AppData\Local\Temp\file.py:10: Warning"],
                }
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False, encoding="utf-8") as f:
            json.dump(nb, f)
            f_path = f.name
        try:
            assert sanitize_notebook_paths(f_path) is True
            with open(f_path, encoding="utf-8") as f:
                result = json.load(f)
            text = result["cells"][0]["outputs"][0]["text"][0]
            assert "Users" not in text
            assert "./AppData/Local/Temp/file.py:10: Warning" in text
        finally:
            os.unlink(f_path)

    def test_idempotent(self) -> None:
        nb = self._make_notebook(
            [
                {
                    "output_type": "stream",
                    "name": "stderr",
                    "text": [r"C:\Users\test\file.py:10: Warning"],
                }
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False, encoding="utf-8") as f:
            json.dump(nb, f)
            f_path = f.name
        try:
            assert sanitize_notebook_paths(f_path) is True
            assert sanitize_notebook_paths(f_path) is False
        finally:
            os.unlink(f_path)

    def test_skips_non_ipynb(self) -> None:
        assert sanitize_notebook_paths("test.py") is False

    def test_no_modification_when_clean(self) -> None:
        nb = self._make_notebook(
            [
                {
                    "output_type": "stream",
                    "name": "stdout",
                    "text": ["Hello world\n"],
                }
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False, encoding="utf-8") as f:
            json.dump(nb, f)
            f_path = f.name
        try:
            assert sanitize_notebook_paths(f_path) is False
        finally:
            os.unlink(f_path)

    @pytest.mark.parametrize(
        "path,expected_clean",
        [
            (r"C:\Users\alice\project\main.py", "./project/main.py"),
            ("/home/bob/src/test.py", "./src/test.py"),
            ("/Users/charlie/docs/readme.md", "./docs/readme.md"),
        ],
    )
    def test_various_platforms(self, path: str, expected_clean: str) -> None:
        nb = self._make_notebook([{"output_type": "stream", "name": "stderr", "text": [path]}])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False, encoding="utf-8") as f:
            json.dump(nb, f)
            f_path = f.name
        try:
            sanitize_notebook_paths(f_path)
            with open(f_path, encoding="utf-8") as f:
                result = json.load(f)
            assert result["cells"][0]["outputs"][0]["text"][0] == expected_clean
        finally:
            os.unlink(f_path)

    def test_sanitizes_traceback_field(self) -> None:
        nb = self._make_notebook(
            [
                {
                    "output_type": "error",
                    "ename": "FileNotFoundError",
                    "evalue": r"C:\Users\testuser\missing.py",
                    "traceback": [
                        r'File "C:\Users\testuser\project\main.py", line 5',
                        r"  in C:\Users\testuser\project\utils.py",
                    ],
                }
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False, encoding="utf-8") as f:
            json.dump(nb, f)
            f_path = f.name
        try:
            assert sanitize_notebook_paths(f_path) is True
            with open(f_path, encoding="utf-8") as f:
                result = json.load(f)
            output = result["cells"][0]["outputs"][0]
            assert "Users" not in output["evalue"]
            assert output["evalue"] == "./missing.py"
            for line in output["traceback"]:
                assert "Users" not in line
        finally:
            os.unlink(f_path)

    def test_skips_binary_mime_types(self) -> None:
        nb = self._make_notebook(
            [
                {
                    "output_type": "display_data",
                    "data": {
                        "image/png": "iVBORw0KGgoAAAANSUhEUgA/Users/fake/path",
                        "text/plain": r"C:\Users\testuser\image.png",
                    },
                }
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False, encoding="utf-8") as f:
            json.dump(nb, f)
            f_path = f.name
        try:
            sanitize_notebook_paths(f_path)
            with open(f_path, encoding="utf-8") as f:
                result = json.load(f)
            data = result["cells"][0]["outputs"][0]["data"]
            # image/png should be untouched
            assert "/Users/fake/path" in data["image/png"]
            # text/plain should be sanitized
            assert "Users" not in data["text/plain"]
        finally:
            os.unlink(f_path)

    def test_preserves_unicode_in_output(self) -> None:
        """Verify non-ASCII characters like emojis are not escaped in the output file."""
        nb = self._make_notebook(
            [
                {
                    "output_type": "stream",
                    "name": "stdout",
                    "text": [
                        "🔹 Result: passed\n",
                        r"C:\Users\testuser\project\file.py",
                    ],
                }
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False, encoding="utf-8") as f:
            json.dump(nb, f)
            f_path = f.name
        try:
            sanitize_notebook_paths(f_path)
            # Check raw file contents to ensure unicode is not escaped
            with open(f_path, encoding="utf-8") as f:
                raw_content = f.read()
            assert "🔹" in raw_content
            assert "\\ud83d" not in raw_content  # should not be JSON-escaped
        finally:
            os.unlink(f_path)

    def test_skips_application_json_mime_type(self) -> None:
        """Verify application/json data outputs are not sanitized since they may be nested dicts."""
        nb = self._make_notebook(
            [
                {
                    "output_type": "execute_result",
                    "data": {
                        "application/json": {"path": r"C:\Users\testuser\data.json"},
                        "text/plain": r"C:\Users\testuser\data.json",
                    },
                }
            ]
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False, encoding="utf-8") as f:
            json.dump(nb, f)
            f_path = f.name
        try:
            sanitize_notebook_paths(f_path)
            with open(f_path, encoding="utf-8") as f:
                result = json.load(f)
            data = result["cells"][0]["outputs"][0]["data"]
            # application/json should be untouched
            assert data["application/json"]["path"] == r"C:\Users\testuser\data.json"
            # text/plain should be sanitized
            assert "Users" not in data["text/plain"]
        finally:
            os.unlink(f_path)
