# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import os
import tempfile

import pytest

from build_scripts.strip_notebook_progress_bars import _is_tqdm_line, strip_notebook_progress_bars


def _make_notebook(outputs: list) -> dict:
    return {"cells": [{"cell_type": "code", "outputs": outputs}]}


def _write_notebook(nb: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".ipynb", delete=False, encoding="utf-8")
    json.dump(nb, f)
    f.close()
    return f.name


class TestIsTqdmLine:
    @pytest.mark.parametrize(
        "line",
        [
            "  45%|████████████████▍                    | 45/100 [00:15<00:18,  2.98it/s]\n",
            "100%|██████████| 100/100 [00:30<00:00,  3.33it/s]\n",
            "  0%|          | 0/50 [00:00<?, ?it/s]\r",
            " 12%|━━━━━                              | 6/50\n",
        ],
    )
    def test_detects_tqdm_lines(self, line: str) -> None:
        assert _is_tqdm_line(line) is True

    @pytest.mark.parametrize(
        "line",
        [
            "INFO: Processing file X\n",
            "WARNING: something happened\n",
            "",
            "\r",
            "  \n",
        ],
    )
    def test_rejects_non_tqdm_lines(self, line: str) -> None:
        assert _is_tqdm_line(line) is False


class TestStripNotebookProgressBars:
    def test_skips_non_ipynb(self) -> None:
        assert strip_notebook_progress_bars("test.py") is False

    def test_no_modification_when_clean(self) -> None:
        nb = _make_notebook([{"output_type": "stream", "name": "stdout", "text": ["Hello\n"]}])
        path = _write_notebook(nb)
        try:
            assert strip_notebook_progress_bars(path) is False
        finally:
            os.unlink(path)

    def test_strips_all_tqdm_stderr(self) -> None:
        nb = _make_notebook(
            [
                {
                    "output_type": "stream",
                    "name": "stderr",
                    "text": [
                        "  0%|          | 0/10 [00:00<?, ?it/s]\n",
                        "100%|██████████| 10/10 [00:05<00:00]\n",
                    ],
                }
            ]
        )
        path = _write_notebook(nb)
        try:
            assert strip_notebook_progress_bars(path) is True
            with open(path, encoding="utf-8") as f:
                result = json.load(f)
            assert result["cells"][0]["outputs"] == []
        finally:
            os.unlink(path)

    def test_keeps_non_tqdm_stderr_lines(self) -> None:
        nb = _make_notebook(
            [
                {
                    "output_type": "stream",
                    "name": "stderr",
                    "text": [
                        "WARNING: something\n",
                        " 50%|█████     | 5/10 [00:02<00:02]\n",
                    ],
                }
            ]
        )
        path = _write_notebook(nb)
        try:
            assert strip_notebook_progress_bars(path) is True
            with open(path, encoding="utf-8") as f:
                result = json.load(f)
            text = result["cells"][0]["outputs"][0]["text"]
            assert text == ["WARNING: something\n"]
        finally:
            os.unlink(path)

    def test_preserves_stdout_and_other_outputs(self) -> None:
        nb = _make_notebook(
            [
                {"output_type": "stream", "name": "stdout", "text": ["hello\n"]},
                {
                    "output_type": "stream",
                    "name": "stderr",
                    "text": ["100%|██████████| 10/10\n"],
                },
                {"output_type": "execute_result", "data": {"text/plain": "42"}},
            ]
        )
        path = _write_notebook(nb)
        try:
            assert strip_notebook_progress_bars(path) is True
            with open(path, encoding="utf-8") as f:
                result = json.load(f)
            outputs = result["cells"][0]["outputs"]
            assert len(outputs) == 2
            assert outputs[0]["name"] == "stdout"
            assert outputs[1]["output_type"] == "execute_result"
        finally:
            os.unlink(path)

    def test_idempotent(self) -> None:
        nb = _make_notebook(
            [
                {
                    "output_type": "stream",
                    "name": "stderr",
                    "text": ["100%|██████████| 10/10\n"],
                }
            ]
        )
        path = _write_notebook(nb)
        try:
            assert strip_notebook_progress_bars(path) is True
            assert strip_notebook_progress_bars(path) is False
        finally:
            os.unlink(path)
