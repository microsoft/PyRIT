# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Migration history must be immutable. This hook enforces that by preventing deletion or updates to migration scripts.

Checks staged changes (local pre-commit), the full branch diff against origin/main (CI PRs),
and the previous commit (CI merge-queue / push-to-main).

The two history checks are skipped on release branches, which legitimately diverge from main.
"""

import os
import subprocess
import sys

_VERSIONS_PATH = "pyrit/memory/alembic/versions/"
_MERGE_QUEUE_REF_PREFIX = "refs/heads/gh-readonly-queue/"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def _git_stdout(*args: str) -> str:
    return _git(*args).stdout.strip()


def _get_violations(diff_spec: list[str]) -> list[str]:
    """Return lines from ``git diff --name-status`` that are not pure additions."""
    output = _git_stdout("diff", "--name-status", *diff_spec, "--", _VERSIONS_PATH)
    return [line for line in output.splitlines() if line and not line.startswith("A")]


def _in_ci() -> bool:
    return os.environ.get("CI", "").lower() in {"1", "true"} or "GITHUB_ACTIONS" in os.environ


def _fail_ci(reason: str) -> bool:
    """Fail closed in CI when we can't perform the check, pass through locally."""
    if _in_ci():
        print(f"[ERROR] Cannot verify alembic revision immutability: {reason}")
        print("        Ensure the CI checkout has full history (fetch-depth: 0).")
        return True
    return False


def _on_release_branch() -> bool:
    """
    Report whether the checks below are running against a release branch.

    A release branch is cut from an earlier tag and carries cherry-picked commits, so it
    legitimately differs from ``main`` in ways the history checks below would read as
    edits to already-released revisions.
    """
    # push events; pull_request events set GITHUB_REF to refs/pull/<n>/merge instead,
    # which carries no branch name, so the target branch has to come from GITHUB_BASE_REF.
    github_ref = os.environ.get("GITHUB_REF", "")
    base_ref = os.environ.get("GITHUB_BASE_REF", "")
    if github_ref or base_ref:
        # merge_group events run on a temporary queue branch named
        # refs/heads/gh-readonly-queue/<target branch>/pr-<n>-<sha> and leave GITHUB_BASE_REF
        # unset, so the target branch has to be read back out of the ref itself.
        if github_ref.startswith(_MERGE_QUEUE_REF_PREFIX):
            github_ref = f"refs/heads/{github_ref[len(_MERGE_QUEUE_REF_PREFIX) :]}"
        return github_ref.startswith("refs/heads/releases/") or base_ref.startswith("releases/")
    # Neither variable is set outside CI, so fall back to the checked-out branch.
    return _git_stdout("rev-parse", "--abbrev-ref", "HEAD").startswith("releases/")


def has_revision_violations() -> bool:
    # Local pre-commit: check staged changes
    violations = _get_violations(["--cached"])
    if violations:
        _report(violations)
        return True

    # A release branch carries cherry-picked fixes that amend already-released revisions on
    # purpose, so comparing it against `main` reports intentional work as violations. A pull
    # request is still comparable against its own base, so only the push and merge queue paths
    # are skipped. `git cherry-pick` does not run pre-commit, so the staged check above rarely
    # fires on those paths either: review is the remaining control there.
    base_ref = os.environ.get("GITHUB_BASE_REF", "")
    if _on_release_branch() and not base_ref:
        return False

    # CI (PR): diff branch against its merge-base with the branch it targets. A pull request
    # into a release branch has to compare against that branch, because everything the release
    # branch already carries is not part of the change under review.
    # The three-dot syntax (A...B) resolves to ``git diff $(merge-base A B) B``
    # automatically, so we don't need a separate merge-base call.  When
    # the base is missing (shallow clone) git exits non-zero.
    base = f"origin/{base_ref}" if base_ref else "origin/main"
    pr_diff = _git("diff", "--name-status", f"{base}...HEAD", "--", _VERSIONS_PATH)
    if pr_diff.returncode == 0:
        violations = [line for line in pr_diff.stdout.strip().splitlines() if line and not line.startswith("A")]
        if violations:
            _report(violations)
            return True
    elif _fail_ci(f"{base} is not available (shallow clone?)"):
        return True

    # CI (merge-queue / push-to-main): on main the branch *is* origin/main, so
    # the diff above is empty.  Compare HEAD against its first parent to catch
    # deletions or modifications introduced by the merge commit itself.
    head_parent = _git("rev-parse", "--verify", "HEAD~1")
    if head_parent.returncode == 0:
        violations = _get_violations(["HEAD~1..HEAD"])
        if violations:
            _report(violations)
            return True
    elif _fail_ci("HEAD~1 is not available (shallow clone?)"):
        return True

    return False


def _report(violations: list[str]) -> None:
    print("[ERROR] Migration scripts can only be added, not modified or deleted.")
    print("The following disallowed changes were detected:")
    for v in violations:
        print(f"  {v}")


if __name__ == "__main__":
    if has_revision_violations():
        sys.exit(1)
