# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Migration history must be immutable. This hook enforces that by preventing deletion or updates to migration scripts.

Checks staged changes (local pre-commit), the full branch diff against origin/main (CI PRs),
and the previous commit (CI merge-queue / push-to-main).
"""

import os
import subprocess
import sys

_VERSIONS_PATH = "pyrit/memory/alembic/versions/"


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


def has_revision_violations() -> bool:
    # Local pre-commit: check staged changes
    violations = _get_violations(["--cached"])
    if violations:
        _report(violations)
        return True

    # CI (PR): check full branch diff against origin/main
    merge_base = _git_stdout("merge-base", "origin/main", "HEAD")
    head_sha = _git_stdout("rev-parse", "HEAD")
    if merge_base and merge_base != head_sha:
        violations = _get_violations([f"{merge_base}...HEAD"])
        if violations:
            _report(violations)
            return True
    elif not merge_base:
        # On CI this is almost always a shallow-clone problem and must not be
        # treated as "no violations".  Locally (e.g. a brand-new repo with no
        # origin/main) it's expected, so we only fail in CI.
        if _fail_ci("git merge-base origin/main HEAD returned empty"):
            return True

    # CI (merge-queue / push-to-main): compare HEAD against its first parent.
    # In a merge queue the branch *is* main, so merge-base == HEAD and the
    # check above produces an empty diff.  Comparing HEAD~1..HEAD catches
    # deletions or modifications introduced by the merge commit.
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
