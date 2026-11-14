# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path

import griffe
import pytest
import yaml
from markdown_it import MarkdownIt
from mdit_py_plugins.front_matter import front_matter_plugin

from build_scripts.gen_api_md import render_module
from build_scripts.pydoc2json import module_to_dict
from build_scripts.validate_docs import parse_toc_files

REPO_ROOT = Path(__file__).resolve().parents[3]


def _headings(markdown: str) -> list[tuple[int, str]]:
    tokens = MarkdownIt().use(front_matter_plugin).parse(markdown)
    return [
        (int(token.tag[1:]), tokens[index + 1].content)
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
    ]


def _assert_contiguous_headings(headings: list[tuple[int, str]]) -> None:
    assert headings
    previous = 0
    for level, title in headings:
        assert level <= previous + 1, f"Heading {title!r} skips from depth {previous} to {level}"
        previous = level


@pytest.mark.parametrize("with_function", [False, True])
def test_generated_methods_are_direct_children_of_their_class(with_function: bool) -> None:
    members = [{"name": "Client", "kind": "class", "methods": [{"name": "send_async"}]}]
    if with_function:
        members.insert(0, {"name": "connect_async", "kind": "function"})
    markdown = render_module({"name": "pyrit.example", "members": members})
    headings = _headings(markdown)

    _assert_contiguous_headings(headings)
    assert headings[-2:] == [(2, "`Client`"), (3, "`send_async`")]
    assert "(api-pyrit_example-Client-send_async)=" in markdown


@pytest.mark.parametrize(
    "module",
    ["pyrit.cli.api_client", "pyrit.message_normalizer.generic_system_squash"],
)
def test_source_to_api_markdown_has_contiguous_headings(module: str) -> None:
    path = REPO_ROOT.joinpath(*module.split(".")).with_suffix(".py")
    source = griffe.visit(module, code=path.read_text(encoding="utf-8"), filepath=path)
    markdown = render_module(module_to_dict(source))

    _assert_contiguous_headings(_headings(markdown))
    if module.endswith("generic_system_squash"):
        assert all("Instructions" not in title for _, title in _headings(markdown))
        assert "```text\n### Instructions ###\n{system_content}\n######\n{user_content}\n```" in markdown


def test_azure_ml_guide_keeps_section_targets_within_book_heading_budget() -> None:
    relative_path = "getting_started/troubleshooting/guide_hf_azureml.md"
    markdown = (REPO_ROOT / "doc" / relative_path).read_text(encoding="utf-8")
    config = yaml.safe_load((REPO_ROOT / "doc" / "myst.yml").read_text(encoding="utf-8"))
    assert relative_path in parse_toc_files(config["project"]["toc"])
    headings = _headings(markdown)

    _assert_contiguous_headings(headings)
    assert headings == [
        (1, "Troubleshooting Guide for HF Azure ML Models"),
        (2, "1. ResourceNotReady Error During Azure ML Model Deployment"),
    ]
    for anchor, title in [
        ("symptom", "Symptom:"),
        ("potential-cause", "Potential Cause:"),
        ("solution", "Solution:"),
        ("step-1-check-deployment-logs", "Step 1: Check Deployment Logs"),
        ("step-2-adjust-environment-variable", "Step 2: Adjust Environment Variable"),
        ("step-3-redeploy", "Step 3: Redeploy"),
        ("additional-resources", "Additional Resources:"),
    ]:
        assert f"({anchor})=\n**{title}**" in markdown
