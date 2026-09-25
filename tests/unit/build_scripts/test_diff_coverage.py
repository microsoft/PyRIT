# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import os
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]


def _workflow() -> dict:
    return yaml.load(
        (REPO_ROOT / ".github" / "workflows" / "diff_cover.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def _diff_step() -> dict:
    return next(
        step for step in _workflow()["jobs"]["coverage"]["steps"] if "Check diff coverage" in step.get("name", "")
    )


def _diff_cover_args(*, target: str = "unit-test-diff-cover", baseline: str | None = None) -> list[str]:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    if baseline is None:
        baseline = next(
            line.split("?=", 1)[1] for line in makefile.splitlines() if line.startswith("DIFF_COVER_BASE?=")
        )
    recipe = makefile.split(f"\n{target}:\n", 1)[1].split("\n\n", 1)[0]
    command = next(line.strip() for line in recipe.splitlines() if "diff_cover.diff_cover_tool" in line)
    args = shlex.split(command.replace("$(DIFF_COVER_BASE)", baseline))
    assert args[:3] == ["uv", "run", "python"]
    # Exercise the checked-in recipe with the current uv environment on every OS,
    # without requiring GNU Make in Python-only developer environments.
    return [sys.executable, *args[3:]]


def _git(*, repo: Path, args: list[str]) -> str:
    # Windows can lose restored empty values in the native environment after patch.dict.
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=30, env=dict(os.environ)
    )
    return result.stdout.strip()


def _commit(*, repo: Path, filename: str, content: str) -> None:
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo=repo, args=["add", filename])
    _git(repo=repo, args=["-c", "commit.gpgsign=false", "commit", "-m", filename])


@pytest.fixture(params=["main", "releases/test-base"])
def merged_pr(*, tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    return _make_merged_pr(repo=tmp_path, base_branch=request.param)


def _make_merged_pr(*, repo: Path, base_branch: str, python_change: bool = True) -> Path:
    """Model a detached PR merge with base-only commits before and after checkout."""
    _git(repo=repo, args=["init", "-b", base_branch])
    _git(repo=repo, args=["config", "user.email", "coverage-test@example.com"])
    _git(repo=repo, args=["config", "user.name", "Coverage test"])
    _git(repo=repo, args=["config", "core.autocrlf", "false"])
    _commit(repo=repo, filename="base_only.py", content="value = 1\n")
    _git(repo=repo, args=["checkout", "-b", "pr"])
    if python_change:
        _commit(repo=repo, filename="pr.py", content="".join(f"value_{i} = {i}\n" for i in range(10)))
    else:
        _commit(repo=repo, filename="requirements.txt", content="diff-cover==10.5.1\n")
    _git(repo=repo, args=["checkout", base_branch])
    _commit(repo=repo, filename="base_only.py", content="value = 2\n")
    _git(repo=repo, args=["checkout", "--detach"])
    _git(repo=repo, args=["-c", "commit.gpgsign=false", "merge", "--no-ff", "pr", "-m", "PR merge"])
    merge_sha = _git(repo=repo, args=["rev-parse", "HEAD"])
    _git(repo=repo, args=["branch", "-d", "pr"])
    _git(repo=repo, args=["checkout", base_branch])
    _commit(repo=repo, filename="base_only.py", content="value = 3\n")
    _git(repo=repo, args=["update-ref", f"refs/remotes/origin/{base_branch}", "HEAD"])
    _git(repo=repo, args=["checkout", "--detach", merge_sha])
    return repo


def _write_coverage(*, repo: Path, covered_lines: int) -> None:
    root = ET.Element("coverage")
    ET.SubElement(ET.SubElement(root, "sources"), "source").text = "."
    classes = ET.SubElement(ET.SubElement(ET.SubElement(root, "packages"), "package"), "classes")
    for filename, line_count, hits in [("pr.py", 10, covered_lines), ("base_only.py", 1, 0)]:
        if not (repo / filename).exists():
            continue
        lines = ET.SubElement(ET.SubElement(classes, "class", filename=filename), "lines")
        for number in range(1, line_count + 1):
            ET.SubElement(lines, "line", number=str(number), hits=str(int(number <= hits)))
    ET.ElementTree(root).write(repo / "coverage.xml", encoding="utf-8")


def _run_diff_cover(*, repo: Path, baseline: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(
        [*_diff_cover_args(baseline=baseline), "--format=json:diff-coverage.json"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        env=dict(os.environ),
    )
    report = json.loads((repo / "diff-coverage.json").read_text(encoding="utf-8"))
    return result, report


@pytest.mark.parametrize("covered_lines", [0, 8, 9, 10])
def test_pr_diff_excludes_base_changes_and_enforces_threshold(*, merged_pr: Path, covered_lines: int) -> None:
    _write_coverage(repo=merged_pr, covered_lines=covered_lines)
    result, report = _run_diff_cover(repo=merged_pr, baseline=_diff_step()["env"]["DIFF_COVER_BASE"])

    assert result.returncode == (0 if covered_lines >= 9 else 1), result.stdout + result.stderr
    assert set(report["src_stats"]) == {"pr.py"}
    assert report["total_num_lines"] == 10
    assert report["total_percent_covered"] == covered_lines * 10
    if covered_lines < 9:
        assert "Failure. Coverage is below 90%." in result.stderr


def test_moving_two_dot_baseline_reproduces_false_failure(merged_pr: Path) -> None:
    _write_coverage(repo=merged_pr, covered_lines=9)
    base_ref = _git(repo=merged_pr, args=["for-each-ref", "--format=%(refname)", "refs/remotes/origin"])
    result, report = _run_diff_cover(repo=merged_pr, baseline=base_ref)

    assert result.returncode == 1
    assert set(report["src_stats"]) == {"pr.py", "base_only.py"}
    assert report["total_num_lines"] == 11
    assert report["total_percent_covered"] == 81


def test_dependency_only_pr_has_no_changed_python_lines(tmp_path: Path) -> None:
    repo = _make_merged_pr(repo=tmp_path, base_branch="main", python_change=False)
    _write_coverage(repo=repo, covered_lines=0)
    result, report = _run_diff_cover(repo=repo, baseline=_diff_step()["env"]["DIFF_COVER_BASE"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["src_stats"] == {}
    assert report["total_num_lines"] == 0


def test_missing_baseline_fails_instead_of_passing(merged_pr: Path) -> None:
    _write_coverage(repo=merged_pr, covered_lines=10)
    result = subprocess.run(
        _diff_cover_args(baseline="missing-base"),
        cwd=merged_pr,
        capture_output=True,
        text=True,
        timeout=30,
        env=dict(os.environ),
    )
    assert result.returncode != 0
    assert "missing-base" in result.stderr


def test_diff_coverage_preserves_restored_empty_git_config(tmp_path: Path) -> None:
    environment = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "audit.empty",
        "GIT_CONFIG_VALUE_0": "",
    }
    with patch.dict(os.environ, environment):
        with patch.dict(os.environ, {}, clear=True):
            pass

        repo = _make_merged_pr(repo=tmp_path, base_branch="main")
        assert _git(repo=repo, args=["config", "--get", "audit.empty"]) == ""
        _write_coverage(repo=repo, covered_lines=9)
        result, report = _run_diff_cover(repo=repo, baseline=_diff_step()["env"]["DIFF_COVER_BASE"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert set(report["src_stats"]) == {"pr.py"}
    assert report["total_num_lines"] == 10
    assert report["total_percent_covered"] == 90


def test_workflow_preserves_checkout_scope_and_overall_coverage() -> None:
    workflow = _workflow()
    steps = workflow["jobs"]["coverage"]["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"fetch-depth": "0"}
    assert workflow["on"]["pull_request"]["branches"] == ["main"]
    assert workflow["on"]["push"]["branches"] == ["main", "releases/v*"]
    assert "merge_group" in workflow["on"]
    assert "workflow_dispatch" in workflow["on"]
    assert _diff_step()["if"] == "github.event_name == 'pull_request'"
    assert _diff_step()["run"] == "make unit-test-diff-cover"
    assert _diff_step()["env"] == {"DIFF_COVER_BASE": "HEAD^1"}
    coverage = next(step for step in steps if step.get("run") == "make unit-test-cov-xml")
    assert "if" not in coverage
    assert coverage["env"] == {"COVERAGE_CORE": "sysmon"}
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\nunit-test-cov-xml:\n", 1)[1].split("\n\n", 1)[0]
    assert "--cov-fail-under=78" in recipe


@pytest.mark.parametrize("target", ["diff-cover", "unit-test-diff-cover"])
def test_local_diff_coverage_defaults_and_override(target: str) -> None:
    default_args = _diff_cover_args(target=target)
    assert "--compare-branch=origin/main" in default_args
    assert "--diff-range-notation=.." in default_args
    assert "--fail-under=90" in default_args
    assert "--compare-branch=HEAD^1" in _diff_cover_args(target=target, baseline="HEAD^1")
