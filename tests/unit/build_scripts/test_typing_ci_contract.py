# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def workflow() -> dict[str, Any]:
    data = yaml.load(
        (REPO_ROOT / ".github" / "workflows" / "build_and_test.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert isinstance(data, dict)
    return data


def test_canonical_typing_runs_once_in_ubuntu_pre_commit(*, workflow: dict[str, Any]) -> None:
    typing_steps = [
        (name, step) for name, job in workflow["jobs"].items() for step in job["steps"] if step.get("run") == "make ty"
    ]
    assert len(typing_steps) == 1
    job_name, step = typing_steps[0]
    assert job_name == "pre-commit"
    assert step["if"] == "matrix.os == 'ubuntu-latest'"
    assert workflow["jobs"][job_name]["strategy"]["matrix"]["os"].count("ubuntu-latest") == 1


def test_typing_job_uses_minimum_python_and_all_extras(*, workflow: dict[str, Any]) -> None:
    assert workflow["env"]["PRE_COMMIT_PYTHON_VERSION"] == "3.11"
    steps = workflow["jobs"]["pre-commit"]["steps"]
    setup = next(step for step in steps if step.get("uses", "").startswith("actions/setup-python@"))
    assert setup["with"]["python-version"] == "${{ env.PRE_COMMIT_PYTHON_VERSION }}"
    install = next(step for step in steps if step.get("run") == "uv sync --extra all")
    typing = next(step for step in steps if step.get("run") == "make ty")
    assert steps.index(setup) < steps.index(install) < steps.index(typing)


def test_typing_job_covers_test_only_prs_merge_queue_and_main(*, workflow: dict[str, Any]) -> None:
    events = workflow["on"]
    assert "main" in events["pull_request"]["branches"]
    assert "main" in events["push"]["branches"]
    assert "merge_group" in events
    for event in ("pull_request", "push"):
        assert "paths" not in events[event]
        assert "paths-ignore" not in events[event]
    assert "if" not in workflow["jobs"]["pre-commit"]


def test_canonical_scope_and_production_hook_remain_separate() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "PYMODULE:=pyrit\n" in makefile
    assert "UNIT_TESTS:=tests/unit\n" in makefile
    assert "ty:\n\tuv run --frozen --extra all --link-mode=copy -m ty check $(PYMODULE) $(UNIT_TESTS)\n" in makefile
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hook = next(hook for repo in config["repos"] for hook in repo["hooks"] if hook["id"] == "ty-check")
    assert hook["entry"] == "uv run --frozen --extra all --link-mode=copy ty check pyrit"
    assert hook["files"] == "^pyrit/"
