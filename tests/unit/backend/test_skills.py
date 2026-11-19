# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
from pathlib import Path

import pytest
import yaml

import pyrit.backend
from pyrit.backend.main import app as backend_app
from pyrit.backend.rest_tools import RestToolset

SKILLS_ROOT = Path(pyrit.backend.__file__).parent / "skills"
SKILL_NAMES = ["pyrit-technique"]


def _read_skill(name: str) -> tuple[dict[str, object], str]:
    text = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---\n", 2)
    return yaml.safe_load(frontmatter), body


def test_skills_directory_holds_the_shipped_skills() -> None:
    assert sorted(path.parent.name for path in SKILLS_ROOT.glob("*/SKILL.md")) == SKILL_NAMES


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_frontmatter_follows_the_agent_skills_format(name: str) -> None:
    frontmatter, _ = _read_skill(name)

    # Agent Skills: the name matches its folder and uses lowercase letters, digits, and single hyphens.
    assert frontmatter["name"] == name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64
    assert isinstance(frontmatter["description"], str) and 0 < len(frontmatter["description"]) <= 1024


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_names_only_tools_the_mcp_endpoint_offers(name: str) -> None:
    _, body = _read_skill(name)
    tools_section = body.split("## Tools\n", 1)[1].split("\n## ", 1)[0]
    named_tools = re.findall(r"^- `(\w+)`$", tools_section, flags=re.MULTILINE)
    used_tools = re.findall(r"`((?:list|get|start|query)_\w+)`", body)
    offered = {operation.name for operation in RestToolset(app=backend_app).operations}

    assert named_tools
    assert set(named_tools) <= offered
    # A tool used in the instructions must also be in the list, so a misspelled name fails here.
    assert set(used_tools) <= set(named_tools)
