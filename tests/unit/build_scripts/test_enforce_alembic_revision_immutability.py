# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from build_scripts.enforce_alembic_revision_immutability import (
    _on_release_branch,
    has_revision_violations,
)

MODIFIED_REVISION = "M\tpyrit/memory/alembic/versions/b2f4c6a8d1e3_add_conversations_table.py"
REVISION_PATH = "pyrit/memory/alembic/versions/b2f4c6a8d1e3_add_conversations_table.py"


def _repository_git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repository), *args], capture_output=True, text=True, check=True)


def _commit_revision(repository: Path, content: str) -> None:
    (repository / REVISION_PATH).write_text(content, encoding="utf-8")
    _repository_git(repository, "add", ".")
    _repository_git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "Update revision",
    )


@pytest.fixture
def migration_repository(tmp_path: Path) -> Path:
    _repository_git(tmp_path, "init", "--quiet")
    (tmp_path / REVISION_PATH).parent.mkdir(parents=True)
    _commit_revision(tmp_path, "original\n")
    _repository_git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def _check_repository(repository: Path, *, ci: bool = False) -> bool:
    def run_git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(repository), *args], capture_output=True, text=True)

    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP") if key in os.environ}
    if ci:
        environment["CI"] = "true"
    with patch.dict(os.environ, environment, clear=True):
        with patch("build_scripts.enforce_alembic_revision_immutability._git", side_effect=run_git):
            return has_revision_violations()


def test_restoring_exact_ancestral_main_revision_passes(migration_repository: Path) -> None:
    _commit_revision(migration_repository, "accidental change\n")
    _commit_revision(migration_repository, "original\n")
    assert _check_repository(migration_repository) is False


def test_published_main_modification_still_fails(migration_repository: Path) -> None:
    _commit_revision(migration_repository, "modified\n")
    _repository_git(migration_repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert _check_repository(migration_repository) is True


@pytest.mark.parametrize("operation", ["modify", "delete", "rename", "staged"])
def test_existing_revision_changes_still_fail(migration_repository: Path, operation: str) -> None:
    revision = migration_repository / REVISION_PATH
    if operation in {"modify", "staged"}:
        revision.write_text("modified\n", encoding="utf-8")
    elif operation == "delete":
        revision.unlink()
    else:
        revision.rename(revision.with_name("renamed.py"))
    _repository_git(migration_repository, "add", ".")
    if operation != "staged":
        _repository_git(
            migration_repository,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Disallowed revision change",
        )
    assert _check_repository(migration_repository) is True


def test_modifying_new_branch_revision_still_fails(migration_repository: Path) -> None:
    revision = migration_repository / REVISION_PATH
    revision.with_name("new_revision.py").write_text("new\n", encoding="utf-8")
    _commit_revision(migration_repository, "original\n")
    revision.with_name("new_revision.py").write_text("modified new\n", encoding="utf-8")
    _commit_revision(migration_repository, "original\n")
    assert _check_repository(migration_repository) is True


def test_matching_nonancestor_base_does_not_allow_restoration(migration_repository: Path) -> None:
    original = _repository_git(migration_repository, "rev-parse", "HEAD").stdout.strip()
    (migration_repository / "unrelated.txt").write_text("base update\n", encoding="utf-8")
    _commit_revision(migration_repository, "original\n")
    _repository_git(migration_repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    _repository_git(migration_repository, "switch", "--quiet", "--detach", original)
    _commit_revision(migration_repository, "accidental change\n")
    _commit_revision(migration_repository, "original\n")
    assert _check_repository(migration_repository) is True


def test_missing_protected_base_fails_closed_in_ci(migration_repository: Path) -> None:
    _repository_git(migration_repository, "update-ref", "-d", "refs/remotes/origin/main")
    assert _check_repository(migration_repository, ci=True) is True


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr="")


@pytest.mark.parametrize(
    "environment, expected",
    [
        ({"GITHUB_REF": "refs/heads/releases/v1.1.0"}, True),
        ({"GITHUB_REF": "refs/heads/releases/v1.0.1"}, True),
        ({"GITHUB_REF": "refs/heads/main"}, False),
        ({"GITHUB_REF": "refs/heads/releases-notes"}, False),
        ({"GITHUB_REF": "refs/tags/v1.1.0"}, False),
        ({"GITHUB_REF": "refs/pull/42/merge", "GITHUB_BASE_REF": "releases/v1.1.0"}, True),
        ({"GITHUB_REF": "refs/pull/42/merge", "GITHUB_BASE_REF": "main"}, False),
        ({"GITHUB_REF": "refs/heads/gh-readonly-queue/releases/v1.1.0/pr-42-abc123"}, True),
        ({"GITHUB_REF": "refs/heads/gh-readonly-queue/main/pr-42-abc123"}, False),
        ({}, False),
    ],
)
def test_on_release_branch_recognizes_release_refs(environment: dict[str, str], expected: bool) -> None:
    """pull_request events carry the target branch in GITHUB_BASE_REF; merge_group embeds it in GITHUB_REF."""
    with patch.dict("os.environ", environment, clear=True):
        with patch("build_scripts.enforce_alembic_revision_immutability._git_stdout", return_value="main"):
            assert _on_release_branch() is expected


@pytest.mark.parametrize(
    "checked_out_branch, expected",
    [("releases/v1.1.0", True), ("main", False), ("HEAD", False)],
)
def test_on_release_branch_falls_back_to_checked_out_branch(checked_out_branch: str, expected: bool) -> None:
    """Runs outside GitHub Actions have no ref variables, leaving the branch name as the only signal."""
    with patch.dict("os.environ", {}, clear=True):
        with patch(
            "build_scripts.enforce_alembic_revision_immutability._git_stdout",
            return_value=checked_out_branch,
        ) as mock_git_stdout:
            assert _on_release_branch() is expected

    assert mock_git_stdout.call_args.args == ("rev-parse", "--abbrev-ref", "HEAD")


def test_on_release_branch_ignores_branch_name_when_ci_refs_are_present() -> None:
    """A PR from a release-named source branch into main must still be enforced."""
    environment = {"GITHUB_REF": "refs/pull/42/merge", "GITHUB_BASE_REF": "main"}
    with patch.dict("os.environ", environment, clear=True):
        with patch(
            "build_scripts.enforce_alembic_revision_immutability._git_stdout",
            return_value="releases/v1.1.0",
        ) as mock_git_stdout:
            assert _on_release_branch() is False

    mock_git_stdout.assert_not_called()


def test_release_branch_push_skips_history_checks() -> None:
    """A release branch push shares neither origin/main nor a comparable previous commit."""

    def _fail_if_called(*args, **kwargs):
        raise AssertionError(f"history check ran on a release branch push: {args}")

    with patch.dict(os.environ, {"GITHUB_BASE_REF": ""}, clear=False):
        with patch("build_scripts.enforce_alembic_revision_immutability._on_release_branch", return_value=True):
            with patch("build_scripts.enforce_alembic_revision_immutability._get_violations", return_value=[]):
                with patch("build_scripts.enforce_alembic_revision_immutability._git", side_effect=_fail_if_called):
                    assert has_revision_violations() is False


def test_release_pull_request_compares_against_its_base() -> None:
    """A pull request into a release branch is comparable against that branch."""
    calls: list[tuple] = []

    def _record(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.dict(os.environ, {"GITHUB_BASE_REF": "releases/v1.1.0"}, clear=False):
        with patch("build_scripts.enforce_alembic_revision_immutability._on_release_branch", return_value=True):
            with patch("build_scripts.enforce_alembic_revision_immutability._get_violations", return_value=[]):
                with patch("build_scripts.enforce_alembic_revision_immutability._git", side_effect=_record):
                    assert has_revision_violations() is False

    assert any("origin/releases/v1.1.0...HEAD" in call for call in calls)
    assert not any("origin/main...HEAD" in call for call in calls)


def test_release_pull_request_reports_modified_revision() -> None:
    """The base comparison still catches a revision the pull request itself edits."""

    def _modified(*args, **kwargs):
        if "diff" in args and any(arg == "origin/releases/v1.1.0...HEAD" for arg in args):
            return SimpleNamespace(returncode=0, stdout=f"M\t{MODIFIED_REVISION}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.dict(os.environ, {"GITHUB_BASE_REF": "releases/v1.1.0"}, clear=False):
        with patch("build_scripts.enforce_alembic_revision_immutability._on_release_branch", return_value=True):
            with patch("build_scripts.enforce_alembic_revision_immutability._get_violations", return_value=[]):
                with patch("build_scripts.enforce_alembic_revision_immutability._git", side_effect=_modified):
                    assert has_revision_violations() is True


def test_release_branch_still_reports_staged_violations() -> None:
    """Skipping the history checks must not stop the staged-change check."""
    with patch("build_scripts.enforce_alembic_revision_immutability._on_release_branch", return_value=True):
        with patch(
            "build_scripts.enforce_alembic_revision_immutability._get_violations",
            return_value=[MODIFIED_REVISION],
        ):
            assert has_revision_violations() is True


def test_branch_comparison_still_runs_off_release_branches() -> None:
    """Positive control: the origin/main comparison must keep catching violations everywhere else."""
    with patch.dict(os.environ, {"GITHUB_BASE_REF": ""}, clear=False):
        with patch("build_scripts.enforce_alembic_revision_immutability._on_release_branch", return_value=False):
            with patch("build_scripts.enforce_alembic_revision_immutability._get_violations", return_value=[]):
                with patch(
                    "build_scripts.enforce_alembic_revision_immutability._git",
                    return_value=_completed(stdout=MODIFIED_REVISION),
                ) as mock_git:
                    assert has_revision_violations() is True

    assert mock_git.call_args.args[:3] == ("diff", "--name-status", "origin/main...HEAD")


def test_previous_commit_check_still_runs_off_release_branches() -> None:
    """Positive control: the HEAD~1..HEAD check must keep catching violations everywhere else."""

    def _violations_for(diff_spec: list[str]) -> list[str]:
        return [MODIFIED_REVISION] if diff_spec == ["HEAD~1..HEAD"] else []

    with patch("build_scripts.enforce_alembic_revision_immutability._on_release_branch", return_value=False):
        with patch(
            "build_scripts.enforce_alembic_revision_immutability._get_violations",
            side_effect=_violations_for,
        ):
            with patch("build_scripts.enforce_alembic_revision_immutability._git", return_value=_completed()):
                assert has_revision_violations() is True


def test_clean_history_off_release_branches_passes() -> None:
    with patch("build_scripts.enforce_alembic_revision_immutability._on_release_branch", return_value=False):
        with patch("build_scripts.enforce_alembic_revision_immutability._get_violations", return_value=[]):
            with patch("build_scripts.enforce_alembic_revision_immutability._git", return_value=_completed()):
                assert has_revision_violations() is False
