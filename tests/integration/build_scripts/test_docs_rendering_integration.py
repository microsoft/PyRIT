# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exercise notebook anchors through the locked renderer, not just a synthetic AST."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from unit.async_utils import TEST_WATCHDOG_SECONDS

REPO_ROOT = Path(__file__).resolve().parents[3]


def _nodes(node: dict) -> list[dict]:
    return [node, *(descendant for child in node.get("children", []) for descendant in _nodes(child))]


def _cell(*, identifier: str, source: str, code: bool = False, metadata: dict | None = None) -> dict:
    cell = {
        "id": identifier,
        "cell_type": "code" if code else "markdown",
        "source": source,
        "metadata": metadata or {},
    }
    if code:
        cell.update(
            execution_count=7,
            outputs=[{"output_type": "stream", "name": "stdout", "text": "retained output\n"}],
        )
    return cell


@pytest.mark.parametrize("with_plugin", [False, True])
@pytest.mark.run_only_if_all_tests
def test_numeric_notebook_references_render(tmp_path: Path, with_plugin: bool) -> None:
    plugin = REPO_ROOT / "doc" / "_plugins" / "notebook_identifiers.mjs"
    (tmp_path / "_plugins").mkdir()
    shutil.copyfile(plugin, tmp_path / "_plugins" / "notebook_identifiers.mjs")
    config = """version: 1
project:
  title: Anchor fixture
  toc:
    - file: index.md
    - file: one.ipynb
    - file: two.ipynb
site:
  template: book-theme
"""
    if with_plugin:
        config = config.replace("  title:", "  plugins: [_plugins/notebook_identifiers.mjs]\n  title:")
    (tmp_path / "myst.yml").write_text(config, encoding="utf-8")
    (tmp_path / "index.md").write_text(
        "# References\n\n"
        "[one markdown](one.ipynb#0) [one output](one.ipynb#1) "
        "[two markdown](two.ipynb#0) [two output](two.ipynb#1) "
        "[caption](one.ipynb#2) [authored](one.ipynb#authored)\n"
        "[extensionless](one#0) [root](/one.ipynb#0)\n",
        encoding="utf-8",
    )
    for name, other in [("one", "two"), ("two", "one")]:
        notebook = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {
                "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                "jupytext": {"formats": "ipynb,py:percent", "cell_metadata_filter": "-all", "main_language": "python"},
            },
            "cells": [
                _cell(
                    identifier="0",
                    source=f"# {name.title()}\n\n[local](#0) [output](#1) {{ref}}`0` "
                    f"[other]({other}.ipynb#0) [other output]({other}.ipynb#1)",
                ),
                _cell(identifier="1", source="print('retained output')", code=True, metadata={"tags": ["hide-input"]}),
                _cell(identifier="2", source="print('caption')", code=True, metadata={"caption": "Saved output"}),
                _cell(identifier=f"{name}-label", source="An authored label.", metadata={"label": "authored"}),
            ],
        }
        # Only one authored target belongs in the global index.
        if name == "two":
            notebook["cells"][-1]["metadata"]["label"] = "two-authored"
        (tmp_path / f"{name}.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
    sources = {path: path.read_bytes() for path in tmp_path.glob("*.ipynb")}
    result = subprocess.run(
        [sys.executable, "-m", "jupyter_book", "build", "--html", "--strict"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=TEST_WATCHDOG_SECONDS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert sources == {path: path.read_bytes() for path in sources}
    log = result.stdout + result.stderr
    assert ("Duplicate identifier in project" in log) is not with_plugin, log
    content = tmp_path / "_build" / "site" / "content"
    pages = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in content.glob("*.json")}
    references = [node for page in pages.values() for node in _nodes(page["mdast"]) if node["type"] == "crossReference"]
    assert len(references) == 18
    assert all(node.get("resolved") for node in references), references
    html = tmp_path / "_build" / "html"
    index = BeautifulSoup((html / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert [index.find("a", string=text)["href"] for text in ["one markdown", "one output", "caption", "authored"]] == [
        "/one#0",
        "/one#1",
        "/one#id-2",
        "/one#authored",
    ]
    assert index.find("a", string="extensionless")["href"] == "/one#0"
    assert index.find("a", string="root")["href"] == "/one#0"
    for name in ["one", "two"]:
        soup = BeautifulSoup((html / name / "index.html").read_text(encoding="utf-8"), "html.parser")
        for identifier in ["0", "1", "2", "id-2", "authored" if name == "one" else "two-authored"]:
            assert soup.find(id=identifier) is not None
        assert "retained output" in soup.get_text()
        other = "two" if name == "one" else "one"
        if with_plugin:
            assert soup.find("a", string="local")["href"] == "#0"
            assert soup.find("a", string="output")["href"] == "#1"
            assert soup.find("a", string="other")["href"] == f"/{other}#0"
            assert soup.find("a", string="other output")["href"] == f"/{other}#1"
    if with_plugin:
        assert index.find("a", string="two markdown")["href"] == "/two#0"
        assert index.find("a", string="two output")["href"] == "/two#1"
