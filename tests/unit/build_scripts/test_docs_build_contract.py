# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from pathlib import Path

import jupytext
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMONS_SOURCE = "https://commons.wikimedia.org/wiki/File:Gorch_Fock_unter_Segeln_Kieler_Foerde_2006.jpg"


def _target(target: str) -> tuple[list[str], list[str]]:
    lines = (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    index = next(index for index, line in enumerate(lines) if line.startswith(f"{target}:"))
    dependencies = lines[index].split(":", 1)[1].split()
    commands = []
    for line in lines[index + 1 :]:
        if not line.startswith("\t"):
            break
        commands.append(line.strip())
    return dependencies, commands


def test_html_build_prepares_api_without_selecting_pdf() -> None:
    dependencies, commands = _target("docs-build")
    assert dependencies == ["docs-api"]
    assert "cd doc && uv run jupyter-book build --html --strict" in commands
    assert all("--all" not in command and "--pdf" not in command for command in commands)
    assert commands[-1] == "uv run python -m build_scripts.generate_rss"
    _, api_commands = _target("docs-api")
    assert "build_scripts.pydoc2json" in api_commands[0]
    assert "build_scripts.gen_api_md" in api_commands[1]


def test_pdf_targets_share_checked_export_and_all_builds_html_first() -> None:
    pdf_dependencies, pdf_commands = _target("docs-build-pdf")
    all_dependencies, all_commands = _target("docs-build-all")
    assert pdf_dependencies == ["docs-api"]
    assert all_dependencies == ["docs-build"]
    assert pdf_commands == all_commands == ["uv run python -m build_scripts.build_docs_pdf"]


@pytest.mark.parametrize(
    ("path", "grids"),
    [
        ("getting_started/README.md", 1),
        ("getting_started/install.md", 2),
        ("getting_started/configuration.md", 1),
        ("code/framework.md", 1),
    ],
)
def test_grids_keep_layout_without_unsupported_gutter(*, path: str, grids: int) -> None:
    content = (REPO_ROOT / "doc" / path).read_text(encoding="utf-8")
    assert content.count("{grid}") == grids
    assert ":gutter:" not in content


def test_modality_feedback_citation_and_paired_cells() -> None:
    path = REPO_ROOT / "doc" / "code" / "executor" / "8_modality_feedback"
    notebook = json.loads(path.with_suffix(".ipynb").read_text(encoding="utf-8"))
    companion = jupytext.read(path.with_suffix(".py"), fmt="py:percent")
    assert [cell["source"].rstrip() for cell in companion.cells] == [
        "".join(cell["source"]).rstrip() for cell in notebook["cells"]
    ]
    attribution = next(
        "".join(cell["source"]) for cell in notebook["cells"] if COMMONS_SOURCE in "".join(cell["source"])
    )
    assert "Felix Koenig" in attribution
    assert "Ibn Battuta" in attribution
    assert "CC BY-SA 2.5" in attribution
    assert "#/media/" not in attribution
    assert any(cell.get("outputs") for cell in notebook["cells"])
    assert (path.parent / "assets" / "three_masted_ship_color.jpg").is_file()
