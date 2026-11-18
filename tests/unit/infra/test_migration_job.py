# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Prove fail-closed migration decisions using successful read-only predicates."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from infra.pipelines.validate_migration_job import execution_result, job_presence, require_idle
from unit.async_utils import TEST_WATCHDOG_SECONDS
from unit.infra.test_code_deployment import (
    APP_INPUTS,
    BASH,
    COMMON_INPUTS,
    IMAGE,
    JOB_TEMPLATE,
    PIPELINES,
    RESOURCE_GROUP,
)

JOB_ID = f"{RESOURCE_GROUP}/providers/Microsoft.App/jobs/copyrit-test-migrate"
JOB = {"id": JOB_ID, "properties": {"provisioningState": "Succeeded"}}


def _execution(*, status: str | None = "Succeeded", mode: str = "check") -> dict[str, Any]:
    return {
        "id": f"{JOB_ID}/executions/execution-{mode}",
        "properties": {
            "status": status,
            "template": {
                "containers": [
                    {
                        "name": "pyrit-migrate",
                        "image": IMAGE,
                        "args": {
                            "check": ["--check-only"],
                            "upgrade-required": ["--check-upgrade-required"],
                            "migrate": [],
                        }[mode],
                        "env": [
                            {"name": "PYRIT_MODE", "value": "migrate"},
                            {"name": "AZURE_SQL_SERVER", "value": APP_INPUTS["PYRIT_SQL_SERVER_FQDN"]},
                            {"name": "AZURE_SQL_DATABASE", "value": APP_INPUTS["PYRIT_SQL_DATABASE_NAME"]},
                        ],
                    }
                ]
            },
            # ACA does not retain replica details for all completed executions.
            "detailedStatus": {},
        },
    }


def _result(*, execution: object, mode: str = "check") -> str:
    return execution_result(
        execution=execution,
        execution_id=f"{JOB_ID}/executions/execution-{mode}",
        image=IMAGE,
        mode=mode,
        expected_server=APP_INPUTS["PYRIT_SQL_SERVER_FQDN"],
        expected_database=APP_INPUTS["PYRIT_SQL_DATABASE_NAME"],
    )


@pytest.mark.parametrize(("status", "result"), [("Succeeded", "succeeded"), ("Failed", "failed")])
def test_execution_result_verifies_predicate_without_relying_on_replica_retention(*, status: str, result: str) -> None:
    assert _result(execution=_execution(status=status)) == result


@pytest.mark.parametrize("status", ["Unknown", "Stopped", "Degraded", None, ""])
def test_execution_result_rejects_missing_or_unsafe_status(status: str | None) -> None:
    with pytest.raises(ValueError):
        _result(execution=_execution(status=status))


@pytest.mark.parametrize("field", ["template", "status"])
def test_execution_result_requires_complete_terminal_metadata(field: str) -> None:
    execution = _execution(status="Failed")
    del execution["properties"][field]
    with pytest.raises(ValueError):
        _result(execution=execution)


def test_execution_result_rejects_another_jobs_execution() -> None:
    execution = _execution()
    execution["id"] = execution["id"].replace("-migrate/", "-other/")
    with pytest.raises(ValueError, match="identity"):
        _result(execution=execution)


@pytest.mark.parametrize("field", ["image", "args", "env", "command"])
def test_execution_result_rejects_wrong_image_command_or_sql_target(field: str) -> None:
    execution = _execution()
    execution["properties"]["template"]["containers"][0][field] = {
        "image": IMAGE.replace("a", "b"),
        "args": ["--check-upgrade-required"],
        "env": [{"name": "AZURE_SQL_DATABASE", "value": "another-database"}],
        "command": ["arbitrary-command"],
    }[field]
    with pytest.raises(ValueError):
        _result(execution=execution)


@pytest.mark.parametrize("runtime_mode", [None, "gui", "jupyter"])
def test_upgrade_predicate_requires_migration_entrypoint_mode(runtime_mode: str | None) -> None:
    execution = _execution(mode="upgrade-required")
    environment = execution["properties"]["template"]["containers"][0]["env"]
    environment[:] = [entry for entry in environment if entry["name"] != "PYRIT_MODE"]
    if runtime_mode is not None:
        environment.append({"name": "PYRIT_MODE", "value": runtime_mode})
    with pytest.raises(ValueError, match="execution mode"):
        _result(execution=execution, mode="upgrade-required")


@pytest.mark.parametrize(
    "executions",
    [
        None,
        {},
        [{"name": "orphan", "properties": {"status": "Running"}}],
        [{"name": "unknown", "properties": {}}],
        [{"name": "degraded", "properties": {"status": "Degraded"}}],
    ],
)
def test_job_guard_rejects_active_or_unknown_executions(executions: object) -> None:
    with pytest.raises(ValueError):
        require_idle(executions)


def test_job_guard_requires_explicit_absence_and_terminal_history() -> None:
    assert job_presence(jobs=[], job_id=JOB_ID, allow_missing=True) == "missing"
    assert job_presence(jobs=[JOB], job_id=JOB_ID, allow_missing=False) == "present"
    with pytest.raises(ValueError):
        job_presence(jobs=[], job_id=JOB_ID, allow_missing=False)
    with pytest.raises(ValueError):
        job_presence(jobs=None, job_id=JOB_ID, allow_missing=True)
    with pytest.raises(ValueError):
        job_presence(jobs=[{"id": ""}], job_id=JOB_ID, allow_missing=True)
    require_idle([{"name": "old-execution", "properties": {"status": "Succeeded"}}])


def _run_shell(*, command: str, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    inputs = {key: value for key, value in os.environ.items() if not key.startswith("PYRIT_") and key != "BASH_ENV"}
    return subprocess.run(
        [BASH, "--noprofile", "--norc", "-s"],
        input=(
            "set -euo pipefail\n"
            f'python3() {{ {shlex.quote(Path(sys.executable).as_posix())} -B -I -S "$@"; }}\n'
            f"source {shlex.quote((PIPELINES / 'deploy_app.sh').as_posix())}\n"
            f"expected_job_id={shlex.quote(JOB_ID)}\n"
            f"immutable_image={shlex.quote(IMAGE)}\n"
            "deployment_name=test deployment_tags='{}'\n" + command
        ),
        text=True,
        capture_output=True,
        check=False,
        timeout=TEST_WATCHDOG_SECONDS,
        env={
            **inputs,
            **COMMON_INPUTS,
            **APP_INPUTS,
            "MSYS2_ARG_CONV_EXCL": "*",
            "TEST_TEMPLATE": json.dumps(JOB_TEMPLATE),
            "TEST_JOBS": json.dumps([JOB]),
            **(environment or {}),
        },
    )


@pytest.mark.skipif(BASH is None, reason="Native Bash is required")
@pytest.mark.parametrize(
    ("status", "confirmation", "stopped"),
    [
        ("Succeeded", None, False),
        ("Failed", "Succeeded", True),
        ("Failed", "Failed", False),
        ("Failed", "Unknown", False),
        ("Failed", None, False),
        ("Unknown", None, False),
        (None, None, False),
    ],
)
def test_app_stops_only_for_successful_upgrade_predicate(
    *, status: str | None, confirmation: str | None, stopped: bool
) -> None:
    result = _run_shell(
        environment={
            "TEST_EXECUTION": json.dumps(_execution(status=status)),
            "TEST_CONFIRMATION": json.dumps(_execution(status=confirmation, mode="upgrade-required")),
            "TEST_MIGRATION": json.dumps(_execution(mode="migrate")),
        },
        command="""
for step in validate_app_inputs initialize_deployment_scope read_existing_topology read_app_access_mode \
  build_app_parameters prepare_database_migration start_unchanged_revision verify_readiness \
  wait_for_runtime_readiness warn_if_sql_public preview_deployment; do
  eval "$step() { :; }"
done
stop_app() { echo STOP_APP; }
az() {
  case "$1 $2 $3" in
    "containerapp job show") printf '%s' "$TEST_TEMPLATE";;
    "containerapp job list") printf '%s' "$TEST_JOBS";;
    "containerapp job start") echo "START:$mode" >&2; echo "execution-$mode";;
    "containerapp job execution")
      if [[ "$4" == list ]]; then echo '[]'; return; fi
      case "$mode" in
        check) printf '%s' "$TEST_EXECUTION";;
        upgrade-required) printf '%s' "$TEST_CONFIRMATION";;
        migrate) printf '%s' "$TEST_MIGRATION";;
      esac ;;
    "deployment group create") echo DEPLOY;;
    *) exit 97;;
  esac
}
revision=r health_url=h egress_ip=e expected_public_access=Enabled
template_file=t parameters=()
main
""",
    )
    assert ("STOP_APP" in result.stdout) is stopped, result.stdout + result.stderr
    assert ("START:migrate" in result.stderr) is stopped
    assert ("DEPLOY" in result.stdout.splitlines()) is (stopped or status == "Succeeded")
    assert result.returncode == (0 if stopped or status == "Succeeded" else 1)
    assert ("START:upgrade-required" in result.stderr) is (status == "Failed")


@pytest.mark.skipif(BASH is None, reason="Native Bash is required")
@pytest.mark.parametrize("action", ["prepare_database_migration", "start_database_job_execution migrate"])
@pytest.mark.parametrize("state", ["Running", "Processing", "Unknown", None])
def test_shared_job_is_not_rewritten_or_started_while_execution_is_active_or_unknown(
    *, action: str, state: str | None
) -> None:
    result = _run_shell(
        environment={
            "TEST_EXECUTIONS": json.dumps([{"name": "orphan", "properties": {"status": state}}]),
        },
        command=f"""
preview_deployment() {{ :; }}
az() {{
  case "$1 $2 $3" in
    "containerapp job list") printf '%s' "$TEST_JOBS";;
    "containerapp job show") printf '%s' "$TEST_TEMPLATE";;
    "containerapp job execution") printf '%s' "$TEST_EXECUTIONS";;
    *) echo MUTATION >&2; return 97;;
  esac
}}
{action}
""",
    )
    assert result.returncode != 0
    assert "not confirmed idle" in result.stdout + result.stderr and "MUTATION" not in result.stderr


@pytest.mark.skipif(BASH is None, reason="Native Bash is required")
def test_job_absence_read_failure_is_not_treated_as_first_deployment() -> None:
    result = _run_shell(command="az() { return 23; }\nassert_migration_job_idle --allow-missing\n")
    assert result.returncode != 0
    assert "Could not establish whether the migration job exists" in result.stdout


@pytest.mark.skipif(BASH is None, reason="Native Bash is required")
@pytest.mark.parametrize(
    ("action", "can_create"), [("prepare_database_migration", True), ("start_database_job_execution migrate", False)]
)
def test_explicit_job_absence_allows_creation_but_never_start(*, action: str, can_create: bool) -> None:
    result = _run_shell(
        command=f"""
preview_deployment() {{ echo PREVIEW; }}
az() {{
  case "$1 $2 $3" in
    "containerapp job list") echo '[]';;
    "containerapp job show") printf '%s' "$TEST_TEMPLATE";;
    *) echo WRITE >&2;;
  esac
}}
{action}
"""
    )
    assert (result.returncode == 0) is can_create, result.stdout + result.stderr
    assert ("WRITE" in result.stderr) is can_create
