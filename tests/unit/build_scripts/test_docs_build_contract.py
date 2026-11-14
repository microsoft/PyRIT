# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from pathlib import Path

import jupytext
import nbformat
import pytest
import yaml
from jupytext.combine import combine_inputs_with_outputs
from jupytext.config import load_jupytext_config, notebook_formats
from jupytext.paired_paths import paired_paths

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
    assert "cd doc && uv run jupyter-book build --html --strict --check-links" in commands
    assert all("--all" not in command and "--pdf" not in command for command in commands)
    assert commands[-1] == "uv run python -m build_scripts.generate_rss"
    _, api_commands = _target("docs-api")
    assert "build_scripts.pydoc2json" in api_commands[0]
    assert "build_scripts.gen_api_md" in api_commands[1]


def test_documentation_config_does_not_select_or_suppress_pdf_exports() -> None:
    config = yaml.safe_load((REPO_ROOT / "doc" / "myst.yml").read_text(encoding="utf-8"))
    assert all(export["format"] != "pdf" for export in config["project"].get("exports", []))
    assert all(rule["rule"] != "tex-renders" for rule in config["project"]["error_rules"])


@pytest.mark.parametrize(
    "url",
    [
        COMMONS_SOURCE,
        "https://crates.io",
        "https://help.openai.com/en/articles/20001152-what-to-know-about-the-sora-discontinuation",
    ],
)
def test_robot_exceptions_only_skip_the_verified_url(url: str) -> None:
    config = yaml.safe_load((REPO_ROOT / "doc" / "myst.yml").read_text(encoding="utf-8"))
    rules = config["project"]["error_rules"]
    exception = next(rule for rule in rules if url in rule.get("keys", []))
    assert exception == {"rule": "link-resolves", "severity": "ignore", "keys": [url]}


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


@pytest.mark.parametrize(
    "relative_path",
    [
        "code/executor/2_multi_turn.ipynb",
        "code/targets/1_openai_chat_target.ipynb",
        "code/converters/0_converters.ipynb",
    ],
)
def test_jupytext_pairing_uses_repository_configuration(relative_path: str) -> None:
    path = REPO_ROOT / "doc" / relative_path
    notebook = jupytext.read(path)
    config = load_jupytext_config(path)
    assert config is not None
    formats = notebook_formats(notebook, config, str(path))
    pairs = paired_paths(str(path), {"extension": ".ipynb"}, formats)
    assert {Path(pair_path) for pair_path, _ in pairs} == {path, path.with_suffix(".py")}
    assert all(Path(pair_path).is_file() for pair_path, _ in pairs)
    assert next(fmt for pair_path, fmt in pairs if Path(pair_path).suffix == ".py")["format_name"] == "percent"


@pytest.mark.parametrize("metadata_filter", ["-all", "class,-all"])
def test_jupytext_metadata_filters_preserve_outputs_during_pairing(metadata_filter: str) -> None:
    notebook = nbformat.v4.new_notebook(
        metadata={
            "jupytext": {"formats": "ipynb,py:percent", "cell_metadata_filter": metadata_filter},
            "language_info": {"name": "python"},
        },
        cells=[
            nbformat.v4.new_code_cell(
                "print('retained output')",
                metadata={"class": "example", "tags": ["hide-input"]},
                execution_count=7,
                outputs=[nbformat.v4.new_output("stream", name="stdout", text="retained output\n")],
            ),
            nbformat.v4.new_code_cell("%%bash\necho 'another language'"),
        ],
    )
    percent = jupytext.writes(notebook, fmt="py:percent")
    companion = jupytext.reads(percent, fmt="py:percent")
    assert companion.cells[0].metadata == ({"class": "example"} if metadata_filter == "class,-all" else {})
    assert companion.metadata.jupytext.main_language == "python"
    assert "kernelspec" not in companion.metadata
    assert companion.metadata.jupytext.formats == "ipynb,py:percent"
    assert companion.metadata.jupytext.cell_metadata_filter == metadata_filter

    combined = combine_inputs_with_outputs(companion, notebook, fmt="py:percent")
    assert combined.cells == notebook.cells
    assert combined.metadata.jupytext.formats == "ipynb,py:percent"
    assert combined.metadata.jupytext.cell_metadata_filter == metadata_filter
