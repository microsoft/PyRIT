# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Run the real merge-group helper offline and preserve CodeQL workflow contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CODEQL_PIN = "24c54180a607b1449ed407dd24f251e4e9147c8d"


@pytest.fixture(scope="module")
def steps() -> dict[str, Any]:
    workflow = yaml.load(
        (REPO_ROOT / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    assert set(workflow["on"]) == {"push", "pull_request", "merge_group", "workflow_dispatch", "schedule"}
    assert workflow["on"]["push"]["branches"] == ["main", "releases/v*"]
    assert workflow["on"]["pull_request"]["branches"] == ["main", "releases/**"]
    assert workflow["on"]["schedule"] == [{"cron": "23 2 * * 1"}]
    assert workflow["permissions"] == {}
    assert set(workflow["jobs"]) == {"analyze"}
    job = workflow["jobs"]["analyze"]
    assert job["name"] == "CodeQL (${{ matrix.language }})"
    assert job["strategy"] == {
        "fail-fast": "false",
        "matrix": {"language": ["actions", "javascript-typescript", "python"]},
    }
    assert job["permissions"] == {"actions": "write", "contents": "read", "security-events": "write"}
    assert "if" not in job and "continue-on-error" not in job
    assert all("continue-on-error" not in step for step in job["steps"])
    return {step["name"]: step for step in job["steps"]}


def test_codeql_merge_group_scenarios(steps: dict[str, Any]) -> None:
    script = steps["Publish merge-group CodeQL results"]["with"]["script"]
    with patch.dict("os.environ", {"CODEQL_TEST_SCRIPT": script}):
        result = subprocess.run(
            ["node", "--test", "--test-reporter=tap", str(Path(__file__).with_suffix(".cjs"))],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# fail 0" in result.stdout and "# skipped 0" in result.stdout


def test_codeql_workflow_analysis_and_upload_wiring(steps: dict[str, Any]) -> None:
    assert steps["Checkout"]["with"] == {"ref": "${{ github.sha }}", "persist-credentials": "false"}
    analyze = steps["Perform CodeQL Analysis"]
    assert "if" not in analyze
    assert analyze["id"] == "analysis"
    assert analyze["uses"] == f"github/codeql-action/analyze@{CODEQL_PIN}"
    assert steps["Initialize CodeQL"]["uses"] == f"github/codeql-action/init@{CODEQL_PIN}"
    assert steps["Initialize CodeQL"]["with"] == {"languages": "${{ matrix.language }}", "build-mode": "none"}
    assert analyze["with"] == {
        "category": "/language:${{ matrix.language }}",
        "upload": "failure-only",
        "post-processed-sarif-path": "${{ runner.temp }}/codeql-upload",
    }
    native = steps["Publish CodeQL results"]
    assert native["uses"] == f"github/codeql-action/upload-sarif@{CODEQL_PIN}"
    assert native["with"] == {
        "sarif_file": "${{ runner.temp }}/codeql-upload/upload.sarif",
        "category": analyze["with"]["category"],
    }
    publish = steps["Publish merge-group CodeQL results"]
    assert publish["uses"] == "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3"
    assert publish["with"]["retries"] == "0"
    assert publish["timeout-minutes"] == "8"
    assert publish["env"] == {
        "ANALYSIS_OUTCOME": "${{ steps.analysis.outcome }}",
        "SARIF_FILE": "${{ runner.temp }}/codeql-upload/upload.sarif",
        "SARIF_CATEGORY": analyze["with"]["category"],
    }
    script = publish["with"]["script"]
    for value in ("codeql_merge_group.cjs", "github, context, core", "await publish(", "process.env.ANALYSIS_OUTCOME"):
        assert value in script
    assert "process.env.GITHUB_RUN_ATTEMPT" in script
    assert "process.env.GITHUB_WORKSPACE" in script
    assert "github-token" not in publish["with"]
    assert not any("setup-uv@" in step.get("uses", "") for step in steps.values())


@pytest.mark.parametrize("event", ["push", "pull_request", "schedule", "workflow_dispatch", "merge_group"])
def test_native_and_merge_group_publishers_are_mutually_exclusive(*, steps: dict[str, Any], event: str) -> None:
    selected = []
    for name, operator in (("Publish CodeQL results", "!="), ("Publish merge-group CodeQL results", "==")):
        assert steps[name]["if"] == f"github.event_name {operator} 'merge_group'"
        if (event == "merge_group") == (operator == "=="):
            selected.append(name)
    assert selected == ["Publish merge-group CodeQL results" if event == "merge_group" else "Publish CodeQL results"]
    # Fork PRs retain the action's native token/ref handling, without a privileged PR trigger.
    assert not {"ref", "sha", "token"} & steps["Publish CodeQL results"]["with"].keys()
