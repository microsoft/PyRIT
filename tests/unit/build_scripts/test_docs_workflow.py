# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
"""Regression contracts for documentation validation and publication."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from build_scripts import compose_docs_dist, inject_version_picker, resolve_docs_matrix

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = REPO_ROOT / ".github" / "docs-versions.yml"
SCRIPT = REPO_ROOT / "build_scripts" / "resolve_docs_matrix.py"
TESTED_SHA = "a" * 40
PUBLISH_IF = (
    "${{ github.ref == 'refs/heads/main' && "
    "(github.event_name == 'push' || github.event_name == 'workflow_dispatch') }}"
)


def _workflow() -> dict[str, Any]:
    return yaml.load(
        (REPO_ROOT / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def _step(*, job: str, name: str) -> dict[str, Any]:
    return next(step for step in _workflow()["jobs"][job]["steps"] if step.get("name") == name)


def _checkout(job: str) -> dict[str, Any]:
    return next(
        step for step in _workflow()["jobs"][job]["steps"] if step.get("uses", "").startswith("actions/checkout@")
    )


def _git(*, repo: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, timeout=30, env=dict(os.environ)
    )
    return result.stdout.strip()


def _commit(*, repo: Path, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo=repo, args=["add", path])
    _git(repo=repo, args=["-c", "commit.gpgsign=false", "commit", "-m", path])


def _run_resolver(*, repo: Path, sha: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(CONFIG),
            "--event-name",
            "pull_request",
            "--ref",
            "refs/pull/42/merge",
            "--sha",
            sha,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        env=dict(os.environ),
    )


@pytest.mark.parametrize("base_branch", ["main", "releases/v1.1.0"])
def test_pr_diff_and_checkout_stay_on_tested_merge(*, tmp_path: Path, base_branch: str) -> None:
    changed_path = "doc/page.md"
    _git(repo=tmp_path, args=["init", "-b", base_branch])
    _git(repo=tmp_path, args=["config", "user.email", "docs-test@example.com"])
    _git(repo=tmp_path, args=["config", "user.name", "Docs test"])
    _commit(repo=tmp_path, path="base.txt", content="original")
    _git(repo=tmp_path, args=["checkout", "-b", "pr"])
    _commit(repo=tmp_path, path=changed_path, content="PR change")
    _git(repo=tmp_path, args=["checkout", base_branch])
    _commit(repo=tmp_path, path=".github/workflows/docs.yml", content="base-only workflow change")
    _git(repo=tmp_path, args=["checkout", "--detach"])
    _git(repo=tmp_path, args=["-c", "commit.gpgsign=false", "merge", "--no-ff", "pr", "-m", "tested merge"])
    tested_sha = _git(repo=tmp_path, args=["rev-parse", "HEAD"])
    _git(repo=tmp_path, args=["checkout", base_branch])
    _commit(repo=tmp_path, path=".github/docs-versions.yml", content="newer base-only config change")
    _git(repo=tmp_path, args=["update-ref", "refs/pull/42/merge", "HEAD"])

    result = _run_resolver(repo=tmp_path, sha=tested_sha)

    assert result.returncode == 0, result.stderr
    outputs = dict(line.split("=", 1) for line in result.stdout.splitlines())
    matrix = json.loads(outputs["matrix"])["include"]
    assert next(entry["ref"] for entry in matrix if entry["slug"] == "latest") == tested_sha
    assert outputs["compose"] == "false"
    assert len(matrix) == 1
    moving_sha = _git(repo=tmp_path, args=["rev-parse", "refs/pull/42/merge"])
    assert tested_sha != moving_sha
    tested_ref = next(entry["ref"] for entry in matrix if entry["slug"] == "latest")
    _git(repo=tmp_path, args=["checkout", "--detach", tested_ref])
    actual_sha = _git(repo=tmp_path, args=["rev-parse", "HEAD"])
    assert actual_sha == tested_sha
    assert (tmp_path / changed_path).read_text(encoding="utf-8") == "PR change"
    key = _step(job="build", name="Restore built site from cache")["with"]["key"]
    assert key.replace("${{ steps.sha.outputs.sha }}", actual_sha) != key.replace(
        "${{ steps.sha.outputs.sha }}", moving_sha
    )


def test_pr_diff_failure_cannot_silently_skip_release_validation(tmp_path: Path) -> None:
    _git(repo=tmp_path, args=["init", "-b", "main"])
    _git(repo=tmp_path, args=["config", "user.email", "docs-test@example.com"])
    _git(repo=tmp_path, args=["config", "user.name", "Docs test"])
    _commit(repo=tmp_path, path="first.txt", content="no first parent available")
    sha = _git(repo=tmp_path, args=["rev-parse", "HEAD"])

    result = _run_resolver(repo=tmp_path, sha=sha)

    assert result.returncode == 1
    assert "cannot compare the tested PR merge with its first parent" in result.stderr
    assert "matrix=" not in result.stdout


def test_workflow_has_isolated_pr_and_serialized_publication_queues() -> None:
    workflow = _workflow()
    assert " ".join(workflow["concurrency"]["group"].split()) == (
        "${{ github.event_name == 'pull_request' && format('docs-pr-{0}', github.event.pull_request.number) || "
        "github.ref == 'refs/heads/main' && 'pages' || format('docs-validation-{0}', github.ref) }}"
    )
    assert workflow["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert all("concurrency" not in job for job in workflow["jobs"].values())


def test_workflow_checks_out_immutable_tested_ref_and_its_parent() -> None:
    assert _checkout("versions")["with"]["ref"] == "${{ github.sha }}"
    assert _checkout("versions")["with"]["fetch-depth"] == "2"
    assert _checkout("compose")["with"]["ref"] == "${{ github.sha }}"
    assert _checkout("build")["with"]["ref"] == "${{ matrix.ref }}"
    command = _step(job="versions", name="Compute matrix")["run"]
    for arg in ['--event-name "$GITHUB_EVENT_NAME"', '--ref "$GITHUB_REF"', '--sha "$GITHUB_SHA"']:
        assert arg in command


def test_rendered_cache_uses_tested_workflow_and_exact_source_commit() -> None:
    workflow = _workflow()
    matrix_step = _step(job="versions", name="Compute matrix")
    assert matrix_step["env"]["WORKFLOW_HASH"] == "${{ hashFiles('.github/workflows/docs.yml') }}"
    assert ".github/workflows/docs.yml" in _checkout("versions")["with"]["sparse-checkout"].splitlines()
    assert 'echo "workflow_hash=$WORKFLOW_HASH" >> "$GITHUB_OUTPUT"' in matrix_step["run"]
    assert workflow["jobs"]["versions"]["outputs"]["workflow_hash"] == "${{ steps.matrix.outputs.workflow_hash }}"
    sha_step = _step(job="build", name="Resolve commit SHA")
    assert sha_step["id"] == "sha"
    assert sha_step["run"] == 'echo "sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"'
    cache = _step(job="build", name="Restore built site from cache")["with"]
    assert cache["key"] == (
        "docs-${{ needs.versions.outputs.workflow_hash }}-${{ matrix.slug }}-${{ steps.sha.outputs.sha }}"
    )
    assert "restore-keys" not in cache


def test_only_main_push_or_dispatch_can_publish_with_write_permissions() -> None:
    workflow = _workflow()
    assert set(workflow["on"]) == {"push", "pull_request", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    for name in ("versions", "build", "compose"):
        assert jobs[name].get("permissions", workflow["permissions"]) == {"contents": "read"}
        assert "environment" not in jobs[name]
    deploy = jobs["deploy"]
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    assert deploy["needs"] == "compose"
    assert " ".join(deploy["if"].split()) == PUBLISH_IF
    upload = _step(job="compose", name="Upload Pages artifact")
    assert " ".join(upload["if"].split()) == PUBLISH_IF
    assert jobs["compose"]["needs"] == ["versions", "build"]
    assert jobs["compose"]["if"] == "needs.versions.outputs.compose == 'true'"
    assert len(deploy["steps"]) == 1
    assert deploy["steps"][0]["uses"].startswith("actions/deploy-pages@")


def test_workflow_preserves_full_build_and_frozen_release_dependencies() -> None:
    jobs = _workflow()["jobs"]
    assert jobs["versions"]["name"] == "Resolve version matrix"
    assert jobs["build"]["name"] == "Build ${{ matrix.slug }}"
    assert jobs["deploy"]["name"] == "Deploy"
    assert jobs["build"]["strategy"]["fail-fast"] == "false"
    assert jobs["build"]["strategy"]["matrix"] == "${{ fromJson(needs.versions.outputs.matrix) }}"
    install = _step(job="build", name="Install PyRIT with dev dependencies")["run"]
    assert "uv sync --frozen $choice" in install
    assert "dependency-groups" in install
    assert "--group dev" in install and "--extra dev" in install
    build = _step(job="build", name="Build the static HTML site")
    assert build["run"] == "uv run --frozen --no-sync jupyter-book build --all --html"
    upload = _step(job="build", name="Upload built site")
    assert upload["with"]["name"] == "site-${{ matrix.slug }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert all("continue-on-error" not in step for job in jobs.values() for step in job["steps"])


def test_docs_and_dependency_inputs_trigger_pr_validation_and_main_publication() -> None:
    events = _workflow()["on"]
    assert events["push"]["branches"] == ["main", "releases/v*"]
    assert events["pull_request"]["branches"] == ["main", "releases/**"]
    assert events["push"]["paths"] == events["pull_request"]["paths"]
    assert set(events["pull_request"]["paths"]) >= {
        ".github/workflows/docs.yml",
        ".github/docs-versions.yml",
        "build_scripts/**",
        "doc/**",
        "pyrit/**",
        "assets/**",
        "tests/unit/build_scripts/**",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "README.md",
        "LICENSE",
        "MANIFEST.in",
    }
    assert "paths-ignore" not in events["push"]


@pytest.mark.parametrize("event_name", ["pull_request", "push", "workflow_dispatch"])
def test_full_matrix_composes_every_version_redirect_and_picker(*, tmp_path: Path, event_name: str) -> None:
    cfg = resolve_docs_matrix.load_config(CONFIG)
    outputs = resolve_docs_matrix.build_outputs(
        cfg=cfg,
        event_name=event_name,
        ref="refs/pull/42/merge" if event_name == "pull_request" else "refs/heads/main",
        sha=TESTED_SHA,
        changed_files=[".github/docs-versions.yml"],
    )
    assert outputs["compose"] == "true"
    artifacts = tmp_path / "artifacts"
    for entry in json.loads(outputs["matrix"])["include"]:
        site = artifacts / f"site-{entry['slug']}"
        site.mkdir(parents=True)
        (site / "index.html").write_text(f"<html><head></head><body>{entry['slug']}</body></html>", encoding="utf-8")
    dist = tmp_path / "dist"

    assert (
        compose_docs_dist.main(
            ["--artifacts-dir", str(artifacts), "--dist-dir", str(dist), "--config", str(CONFIG), "--base", "/PyRIT"]
        )
        == 0
    )
    assert inject_version_picker.main(["--site-dir", str(dist), "--base", "/PyRIT"]) == 0

    assert json.loads((dist / "versions.json").read_text(encoding="utf-8")) == cfg
    for entry in cfg["versions"]:
        html = (dist / entry["slug"] / "index.html").read_text(encoding="utf-8")
        assert f"<body>{entry['slug']}</body>" in html
        assert inject_version_picker.INJECT_MARKER in html
        assert json.loads((dist / entry["slug"] / "pages.json").read_text(encoding="utf-8")) == [""]
    assert f"url=/PyRIT/{cfg['default']}/" in (dist / "index.html").read_text(encoding="utf-8")
    assert f"url=/PyRIT/{cfg['stable']}/" in (dist / "stable" / "index.html").read_text(encoding="utf-8")
    assert "function findClosestPage" in (dist / "404.html").read_text(encoding="utf-8")
