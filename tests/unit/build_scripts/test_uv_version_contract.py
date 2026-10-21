# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_lockfile_selects_one_uv_version() -> None:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = {package["version"] for package in lock["package"] if package["name"] == "uv"}
    assert len(versions) == 1, versions


@pytest.mark.parametrize(
    ("workflow_name", "job_name"),
    [
        ("build_and_test.yml", "openai-compatibility"),
        ("build_and_test.yml", "pre-commit"),
        ("build_and_test.yml", "main-job"),
        ("diff_cover.yml", "coverage"),
        ("frontend_tests.yml", "e2e-shards"),
    ],
)
def test_project_ci_reads_uv_version_from_lockfile(*, workflow_name: str, job_name: str) -> None:
    workflow = yaml.load(
        (REPO_ROOT / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    steps = workflow["jobs"][job_name]["steps"]
    setup_steps = [step for step in steps if step.get("uses", "").startswith("astral-sh/setup-uv@")]
    assert len(setup_steps) == 1
    setup = setup_steps[0]
    assert setup["with"]["version-file"] == "uv.lock"
    assert "version" not in setup["with"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert steps.index(checkout) < steps.index(setup)


@pytest.mark.parametrize("job_name", ["versions", "build", "compose"])
def test_versioned_docs_keep_an_independent_uv_bootstrap(job_name: str) -> None:
    workflow = yaml.load(
        (REPO_ROOT / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    setup = next(
        step for step in workflow["jobs"][job_name]["steps"] if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert setup["with"]["version"]
    assert "version-file" not in setup["with"]
