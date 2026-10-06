# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Offline coverage for Docker CI result gates and container smoke checks."""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docker_build.yml"
PYPI_CONDITION = "github.event_name == 'workflow_dispatch'"
PYPI_GATES = {
    "pypi-production-check": ("Build Production (PyPI)", "production"),
    "pypi-import-check": ("Test Import (PyPI)", "import"),
    "pypi-gui-check": ("Test GUI (PyPI)", "gui"),
    "pypi-jupyter-check": ("Test Jupyter (PyPI)", "jupyter"),
}


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    # BaseLoader preserves the Actions "on" key instead of treating it as a YAML 1.1 boolean.
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


@pytest.fixture(scope="module")
def bash_path() -> str:
    executable = shutil.which("bash")
    git = shutil.which("git")
    if os.name == "nt" and git:
        git_bash = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if git_bash.is_file():
            executable = str(git_bash)
    if executable is None:
        pytest.skip("Bash is required to exercise the Linux Docker CI scripts")
    return executable


def _run_bash(*, bash_path: str, script: str, environment: dict[str, str | None]) -> subprocess.CompletedProcess[str]:
    with patch.dict(os.environ, {key: value for key, value in environment.items() if value is not None}):
        for key, value in environment.items():
            if value is None:
                os.environ.pop(key, None)
        return subprocess.run(
            [bash_path, "-e", "-o", "pipefail", "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )


@pytest.mark.parametrize("gate_id", ["local-checks", *PYPI_GATES])
@pytest.mark.parametrize("job_result", ["success", "failure", "cancelled", "skipped", "", None])
@pytest.mark.parametrize("stage_result", ["success", "failure", "cancelled", "skipped", "", None])
def test_result_gate_fails_closed(
    *, workflow: dict[str, Any], bash_path: str, gate_id: str, job_result: str | None, stage_result: str | None
) -> None:
    gate = workflow["jobs"][gate_id]["steps"][0]
    result = _run_bash(
        bash_path=bash_path,
        script=gate["run"],
        environment={"JOB_RESULT": job_result, "STAGE_RESULT": stage_result},
    )
    assert (result.returncode == 0) == (job_result == "success" and stage_result == "success")
    if result.returncode != 0:
        assert "::error::" in result.stdout


@pytest.mark.parametrize(
    ("source", "names"),
    [
        (
            "local",
            [
                "Build Devcontainer",
                "Build Production (local)",
                "Test Import (local)",
                "Test GUI (local)",
                "Test Jupyter (local)",
            ],
        ),
        ("pypi", ["Build Production (PyPI)", "Test Import (PyPI)", "Test GUI (PyPI)", "Test Jupyter (PyPI)"]),
    ],
)
def test_gate_names_and_outputs_follow_real_stages(*, workflow: dict[str, Any], source: str, names: list[str]) -> None:
    execution_id = f"build-and-test-{source}"
    execution = workflow["jobs"][execution_id]
    if source == "local":
        gate = workflow["jobs"]["local-checks"]
        assert gate["name"] == "${{ matrix.name }}"
        assert gate["strategy"]["fail-fast"] == "false"
        entries = gate["strategy"]["matrix"]["include"]
        assert [entry["name"] for entry in entries] == names
        gates = [(gate, entry["output"]) for entry in entries]
    else:
        gates = [(workflow["jobs"][gate_id], stage) for gate_id, (_, stage) in PYPI_GATES.items()]
        assert [gate["name"] for gate, _ in gates] == names
    steps = {step["id"]: step for step in execution["steps"] if "id" in step}
    for gate, stage in gates:
        assert gate["needs"] == execution_id
        assert "continue-on-error" not in gate
        assert len(gate["steps"]) == 1
        check = gate["steps"][0]
        assert "if" not in check
        assert "continue-on-error" not in check
        output = "[matrix.output]" if source == "local" else f".{stage}"
        assert check["env"] == {
            "JOB_RESULT": "${{ needs." + execution_id + ".result }}",
            "STAGE_RESULT": "${{ needs." + execution_id + ".outputs" + output + " }}",
        }
        assert execution["outputs"][stage] == "${{ steps." + stage + ".outcome }}"
        assert "continue-on-error" not in steps[stage]
    for mode in ("import", "gui", "jupyter"):
        assert steps[mode]["if"] == "${{ !cancelled() && steps.production.outcome == 'success' }}"
        assert steps[mode]["run"] == f"exec bash docker/smoke_test.sh pyrit:{source}-test {mode}"
        assert steps[mode]["timeout-minutes"] == "3"


@pytest.mark.parametrize("failed_source", ["local", "pypi"])
@pytest.mark.parametrize("gate_id", ["local-checks", *PYPI_GATES])
def test_source_failures_do_not_cross_gate_dependencies(
    *, workflow: dict[str, Any], bash_path: str, failed_source: str, gate_id: str
) -> None:
    results = {
        f"build-and-test-{source}": "failure" if source == failed_source else "success" for source in ("local", "pypi")
    }
    gate = workflow["jobs"][gate_id]
    source = "local" if gate_id == "local-checks" else "pypi"
    result = _run_bash(
        bash_path=bash_path,
        script=gate["steps"][0]["run"],
        environment={"JOB_RESULT": results[gate["needs"]], "STAGE_RESULT": "success"},
    )
    assert (result.returncode == 0) == (source != failed_source)


def test_pypi_gates_have_literal_names_for_intentional_skips(workflow: dict[str, Any]) -> None:
    gates = [job for job in workflow["jobs"].values() if job.get("needs") == "build-and-test-pypi"]
    assert [gate["name"] for gate in gates] == [name for name, _ in PYPI_GATES.values()]
    assert all("strategy" not in gate for gate in gates)


def test_workflow_events_and_fork_concurrency_are_preserved(workflow: dict[str, Any]) -> None:
    assert workflow["defaults"]["run"]["shell"] == "bash"
    assert set(workflow["on"]) == {"push", "pull_request", "merge_group", "workflow_dispatch"}
    assert workflow["on"]["push"]["branches"] == ["main", "releases/v*"]
    assert workflow["on"]["pull_request"]["branches"] == ["main", "releases/**"]
    assert workflow["concurrency"] == {
        "group": "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
        "cancel-in-progress": "true",
    }
    assert "if" not in workflow["jobs"]["build-and-test-local"]
    assert workflow["jobs"]["local-checks"]["if"] == "${{ always() }}"
    assert workflow["jobs"]["build-and-test-pypi"]["if"] == PYPI_CONDITION
    for gate_id in PYPI_GATES:
        assert workflow["jobs"][gate_id]["if"] == "${{ always() && (" + PYPI_CONDITION + ") }}"


@pytest.mark.parametrize("job_id", ["build-and-test-pypi", *PYPI_GATES])
@pytest.mark.parametrize(
    ("event", "ref", "enabled"),
    [
        ("pull_request", "refs/pull/123/merge", False),
        ("merge_group", "refs/heads/gh-readonly-queue/main/pr-123-example", False),
        ("push", "refs/heads/releases/v1.2.0", False),
        ("push", "refs/heads/main", False),
        ("workflow_dispatch", "refs/heads/main", True),
        ("workflow_dispatch", "refs/heads/releases/v1.2.0", True),
        ("workflow_dispatch", "refs/heads/docker-ci-test", True),
    ],
)
def test_pypi_execution_and_gates_are_temporarily_manual_only(
    *, workflow: dict[str, Any], bash_path: str, job_id: str, event: str, ref: str, enabled: bool
) -> None:
    condition = workflow["jobs"][job_id]["if"].removeprefix("${{").removesuffix("}}").strip()
    # These comparisons and boolean operators also have Bash conditional syntax.
    condition = condition.replace("always()", "true").replace("github.ref", '"$REF"')
    condition = condition.replace("github.event_name", '"$EVENT"')
    result = _run_bash(
        bash_path=bash_path,
        script=f"[[ {condition} ]]",
        environment={"REF": ref, "EVENT": event},
    )
    assert (result.returncode == 0) == enabled


@pytest.mark.parametrize(
    ("source_status", "status_exit", "commit_exit"),
    [("", 0, 0), (" M pyrit/example.py", 0, 0), ("?? new-file", 0, 0), ("", 128, 0), ("", 0, 128)],
)
def test_local_provenance_requires_clean_checkout_and_successful_git(
    *,
    workflow: dict[str, Any],
    bash_path: str,
    tmp_path: Path,
    source_status: str,
    status_exit: int,
    commit_exit: int,
) -> None:
    steps = workflow["jobs"]["build-and-test-local"]["steps"]
    step = next(step for step in steps if step.get("id") == "provenance")
    base = next(step for step in steps if step.get("id") == "devcontainer")
    production = next(step for step in steps if step.get("id") == "production")
    assert steps.index(step) < steps.index(base) < steps.index(production)
    assert "GIT_COMMIT=${{ steps.provenance.outputs.commit }}" in production["with"]["build-args"]
    assert "GIT_MODIFIED=false" in production["with"]["build-args"]
    commit = "a" * 40
    output = tmp_path / "outputs"
    result = _run_bash(
        bash_path=bash_path,
        script="""
git() {
    case "$*" in
        "status --porcelain") printf '%s' "$SOURCE_STATUS"; return "$STATUS_EXIT";;
        "rev-parse HEAD") printf '%s\\n' "$SOURCE_COMMIT"; return "$COMMIT_EXIT";;
        *) echo "Unexpected Git command" >&2; return 97;;
    esac
}
"""
        + step["run"],
        environment={
            "SOURCE_STATUS": source_status,
            "STATUS_EXIT": str(status_exit),
            "SOURCE_COMMIT": commit,
            "COMMIT_EXIT": str(commit_exit),
            "GITHUB_OUTPUT": output.as_posix(),
        },
    )
    assert (result.returncode == 0) == (source_status == "" and status_exit == 0 and commit_exit == 0)
    if result.returncode == 0:
        assert output.read_text(encoding="utf-8") == f"commit={commit}\n"
    else:
        assert not output.exists()
        if source_status:
            assert "::error::Source checkout must be clean" in result.stdout


def test_pypi_build_defaults_to_latest_release_with_only_an_explicit_override(workflow: dict[str, Any]) -> None:
    version_input = workflow["on"]["workflow_dispatch"]["inputs"]["pypiVersion"]
    assert version_input["type"] == "string"
    assert version_input["required"] == "false"
    steps = workflow["jobs"]["build-and-test-pypi"]["steps"]
    selection = next(step for step in steps if step.get("id") == "pypi-version")
    production = next(step for step in steps if step.get("id") == "production")
    assert selection["env"]["PYRIT_PYPI_VERSION"] == "${{ inputs.pypiVersion }}"
    assert selection["timeout-minutes"] == "5"
    assert "vars.PYRIT_PYPI_VERSION" not in WORKFLOW.read_text(encoding="utf-8")
    assert "python3 build_scripts/select_pypi_version.py" in selection["run"]
    assert "PYRIT_VERSION=${{ steps.pypi-version.outputs.version }}" in production["with"]["build-args"]
    assert steps.index(selection) < steps.index(production)


@pytest.mark.parametrize("override", [None, "", "9.9.9rc1"])
@pytest.mark.parametrize("selection_status", [0, 1])
def test_pypi_selection_outputs_only_a_successfully_resolved_version(
    *, workflow: dict[str, Any], bash_path: str, tmp_path: Path, override: str | None, selection_status: int
) -> None:
    selection = next(
        step for step in workflow["jobs"]["build-and-test-pypi"]["steps"] if step.get("id") == "pypi-version"
    )
    output = tmp_path / "outputs"
    selected = override or "9.9.9"
    result = _run_bash(
        bash_path=bash_path,
        script="""
python3() {
    [[ "$#" == 3 && "$1" == build_scripts/select_pypi_version.py && "$2" == --version &&
        "$3" == "${PYRIT_PYPI_VERSION:-}" ]] || return 97
    printf '%s\\n' "$SELECTED_VERSION"
    return "$SELECTION_STATUS"
}
"""
        + selection["run"],
        environment={
            "PYRIT_PYPI_VERSION": override,
            "SELECTED_VERSION": selected,
            "SELECTION_STATUS": str(selection_status),
            "GITHUB_OUTPUT": output.as_posix(),
        },
    )
    assert result.returncode == selection_status, result.stdout + result.stderr
    if selection_status == 0:
        assert output.read_text(encoding="utf-8") == f"version={selected}\n"
        assert f"Testing published PyPI release: {selected}" in result.stdout
    else:
        assert not output.exists()


def test_builds_stay_local_and_only_identical_devcontainers_share_cache(workflow: dict[str, Any]) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    bases = []
    for source in ("local", "pypi"):
        job = workflow["jobs"][f"build-and-test-{source}"]
        assert "needs" not in job
        assert "permissions" not in job
        assert "timeout-minutes" not in job
        steps = {step["id"]: step for step in job["steps"] if "id" in step}
        bases.append(steps["devcontainer"]["with"])
        for stage in ("devcontainer", "production"):
            assert steps[stage]["with"]["push"] == "false"
            assert steps[stage]["with"]["load"] == "true"
            assert "timeout-minutes" not in steps[stage]
        production = steps["production"]["with"]
        assert production["builder"] == "default"
        assert production["context"] == "."
        assert production["file"] == "docker/Dockerfile"
        assert f"PYRIT_SOURCE={source}" in production["build-args"]
        assert "BASE_IMAGE=pyrit-devcontainer:latest" in production["build-args"]
        assert "cache-to" not in production
    assert bases[0] == bases[1]
    assert bases[0]["context"] == ".devcontainer"
    assert bases[0]["file"] == ".devcontainer/Dockerfile"
    assert bases[0]["cache-from"] == "type=gha"
    assert bases[0]["cache-to"] == "type=gha,mode=max"
    text = WORKFLOW.read_text(encoding="utf-8")
    for handoff in ("docker save", "docker load", "gzip", "actions/upload-artifact", "actions/download-artifact"):
        assert handoff not in text


SMOKE_MOCKS = r"""
record_call() { printf '%s\n' "$*" >> "$CI_TEST_TRACE"; }
docker() {
    record_call docker "$@"
    if [[ "$1" == "${CI_TEST_CLEANUP_STAGE:-}" ]]; then
        record_call cleanup-signal "$CI_TEST_CLEANUP_SIGNAL"
        kill "-$CI_TEST_CLEANUP_SIGNAL" "$$"
        record_call cleanup-signal-ignored "$CI_TEST_CLEANUP_SIGNAL"
    fi
    case "$1" in
        run) [[ "$CI_TEST_CASE" != import-failure ]] ;;
        create)
            [[ "$CI_TEST_CASE" != create-failure ]] || return 41
            echo test-container
            ;;
        start)
            [[ "$CI_TEST_CASE" != start-failure ]] || return 42
            if [[ "$CI_TEST_CASE" == signal ]]; then kill "-$CI_TEST_SIGNAL" "$$"; fi
            ;;
        port) echo 127.0.0.1:49153 ;;
        inspect)
            if [[ "$*" == *'.State.Running'* ]]; then
                [[ "$CI_TEST_CASE" != inspect-failure ]] || return 43
                if [[ "$CI_TEST_CASE" == exited || "$CI_TEST_CASE" == diagnostics-failure ]]; then
                    echo false
                else
                    echo true
                fi
            else
                [[ "$CI_TEST_CASE" != diagnostics-failure ]] || return 44
                echo '{"Status":"exited","ExitCode":1}'
            fi
            ;;
        logs)
            [[ "$CI_TEST_CASE" != diagnostics-failure ]] || return 45
            echo "Application startup failed: multiple Alembic heads (test fixture)"
            ;;
        rm) [[ "$CI_TEST_CASE" != cleanup-failure ]] ;;
        *) echo "Unexpected Docker command: $*" >&2; return 99 ;;
    esac
}
curl() {
    record_call curl "$@"
    if [[ "${!#}" == */ ]]; then
        [[ "$CI_TEST_CASE" != frontend-http-failure ]] || return 22
        if [[ "$CI_TEST_CASE" == missing-frontend ]]; then
            echo '{"detail":"Not Found"}'
        else
            echo '<!DOCTYPE html><html></html>'
        fi
    elif [[ "$CI_TEST_CASE" == timeout ]]; then
        echo "Connection refused" >&2
        return 7
    elif [[ "$CI_TEST_CASE" == redirect ]]; then
        echo 302
    elif [[ "$CI_TEST_CASE" == delayed && $(grep -c '^curl ' "$CI_TEST_TRACE") -lt 3 ]]; then
        return 7
    else
        echo 200
    fi
}
sleep() {
    record_call sleep "$@"
    if [[ "$CI_TEST_CASE" == timeout || "$CI_TEST_CASE" == redirect ]]; then
        SECONDS=$((SECONDS + 120))
    else
        SECONDS=$((SECONDS + 1))
    fi
}
export -f record_call docker curl sleep
exec bash docker/smoke_test.sh "$CI_TEST_IMAGE" "$CI_TEST_MODE" "$CI_TEST_TIMEOUT"
"""


def _run_smoke(
    *,
    bash_path: str,
    tmp_path: Path,
    case: str,
    mode: str,
    source: str = "local",
    timeout: str = "120",
    signal: str = "TERM",
    cleanup_signal: str = "",
    cleanup_stage: str = "",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    trace = tmp_path / "commands.txt"
    result = _run_bash(
        bash_path=bash_path,
        script=SMOKE_MOCKS,
        environment={
            "CI_TEST_TRACE": trace.as_posix(),
            "CI_TEST_CASE": case,
            "CI_TEST_IMAGE": f"pyrit:{source}-test",
            "CI_TEST_MODE": mode,
            "CI_TEST_TIMEOUT": timeout,
            "CI_TEST_SIGNAL": signal,
            "CI_TEST_CLEANUP_SIGNAL": cleanup_signal,
            "CI_TEST_CLEANUP_STAGE": cleanup_stage,
        },
    )
    return result, trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []


@pytest.mark.parametrize("source", ["local", "pypi"])
@pytest.mark.parametrize("mode", ["import", "gui", "jupyter"])
def test_smoke_checks_use_real_image_entrypoints_and_cleanup(
    *, bash_path: str, tmp_path: Path, source: str, mode: str
) -> None:
    result, commands = _run_smoke(bash_path=bash_path, tmp_path=tmp_path, case="ready", mode=mode, source=source)
    assert result.returncode == 0, result.stdout + result.stderr
    if mode == "import":
        assert commands == [
            (
                f"docker run --rm --entrypoint /opt/venv/bin/python pyrit:{source}-test "
                "-c import pyrit; print(f'PyRIT version: {pyrit.__version__}')"
            )
        ]
        return
    port, endpoint = (8000, "/api/health") if mode == "gui" else (8888, "/api")
    assert commands[0] == f"docker create --env PYRIT_MODE={mode} --publish 127.0.0.1::{port} pyrit:{source}-test"
    assert f"docker port test-container {port}/tcp" in commands
    assert any(command.endswith(f"http://127.0.0.1:49153{endpoint}") for command in commands)
    assert commands[-1] == "docker rm --force test-container"
    assert not any(command.startswith(("sleep ", "docker logs ")) for command in commands)
    assert sum(command.startswith("curl ") for command in commands) == (2 if mode == "gui" else 1)


@pytest.mark.parametrize("mode", ["gui", "jupyter"])
def test_readiness_waits_without_restarting_containers(*, bash_path: str, tmp_path: Path, mode: str) -> None:
    result, commands = _run_smoke(bash_path=bash_path, tmp_path=tmp_path, case="delayed", mode=mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert commands.count("sleep 1") == 2
    assert sum(command.startswith("docker create ") for command in commands) == 1
    assert commands[-1] == "docker rm --force test-container"


@pytest.mark.parametrize("case", ["timeout", "redirect"])
@pytest.mark.parametrize("mode", ["gui", "jupyter"])
def test_readiness_is_bounded_and_requires_http_200(*, bash_path: str, tmp_path: Path, case: str, mode: str) -> None:
    result, commands = _run_smoke(bash_path=bash_path, tmp_path=tmp_path, case=case, mode=mode, timeout="3")
    assert result.returncode != 0
    assert "did not become ready within 3s" in result.stdout
    assert "Connection refused" in result.stdout if case == "timeout" else "302" in result.stdout
    requests = [command for command in commands if command.startswith("curl ")]
    assert requests
    assert all("--connect-timeout 2" in command for command in requests)
    assert all(0 < int(re.findall(r"--max-time (\d+)", command)[0]) <= 3 for command in requests)
    assert "docker logs --tail 200 test-container" in commands
    assert commands[-1] == "docker rm --force test-container"


@pytest.mark.parametrize(
    "case",
    [
        "exited",
        "start-failure",
        "inspect-failure",
        "frontend-http-failure",
        "missing-frontend",
        "diagnostics-failure",
        "signal",
    ],
)
def test_smoke_failures_dump_diagnostics_and_remove_container(*, bash_path: str, tmp_path: Path, case: str) -> None:
    result, commands = _run_smoke(bash_path=bash_path, tmp_path=tmp_path, case=case, mode="gui")
    assert result.returncode != 0
    assert "docker inspect --format {{json .State}} test-container" in commands
    assert "docker logs --tail 200 test-container" in commands
    assert commands[-1] == "docker rm --force test-container"
    if case in ("exited", "start-failure", "inspect-failure", "signal"):
        assert not any(command.startswith(("curl ", "sleep ")) for command in commands)
    if case == "exited":
        assert "Container exited" in result.stdout
        assert "multiple Alembic heads" in result.stdout
    if case == "inspect-failure":
        assert "Container exited" not in result.stdout
    if case == "diagnostics-failure":
        assert result.stdout.count("::warning::") == 2
    if case == "signal":
        assert result.returncode == 143


@pytest.mark.parametrize("cleanup_stage", ["logs", "rm"])
@pytest.mark.parametrize("cleanup_signal", ["INT", "TERM"])
@pytest.mark.parametrize(
    ("case", "signal", "expected_status"),
    [("start-failure", "TERM", 42), ("signal", "INT", 130), ("signal", "TERM", 143)],
)
def test_smoke_cleanup_ignores_repeated_signals(
    *,
    bash_path: str,
    tmp_path: Path,
    case: str,
    signal: str,
    expected_status: int,
    cleanup_signal: str,
    cleanup_stage: str,
) -> None:
    result, commands = _run_smoke(
        bash_path=bash_path,
        tmp_path=tmp_path,
        case=case,
        mode="gui",
        signal=signal,
        cleanup_signal=cleanup_signal,
        cleanup_stage=cleanup_stage,
    )
    assert result.returncode == expected_status, result.stdout + result.stderr
    signal_index = commands.index(f"cleanup-signal {cleanup_signal}")
    assert commands[signal_index - 1].startswith(f"docker {cleanup_stage} ")
    assert f"cleanup-signal-ignored {cleanup_signal}" in commands
    assert "docker inspect --format {{json .State}} test-container" in commands
    assert "docker logs --tail 200 test-container" in commands
    assert commands.count("docker rm --force test-container") == 1


@pytest.mark.parametrize(
    ("case", "mode"), [("create-failure", "gui"), ("import-failure", "import"), ("cleanup-failure", "jupyter")]
)
def test_docker_errors_are_not_converted_to_success(*, bash_path: str, tmp_path: Path, case: str, mode: str) -> None:
    result, commands = _run_smoke(bash_path=bash_path, tmp_path=tmp_path, case=case, mode=mode)
    assert result.returncode != 0
    if case == "cleanup-failure":
        assert commands[-1] == "docker rm --force test-container"
        assert "::error::Could not remove container" in result.stdout


@pytest.mark.parametrize(("mode", "timeout"), [("invalid", "120"), ("gui", "0"), ("gui", "-1"), ("gui", "bad")])
def test_invalid_smoke_arguments_fail_before_creating_containers(
    *, bash_path: str, tmp_path: Path, mode: str, timeout: str
) -> None:
    result, commands = _run_smoke(bash_path=bash_path, tmp_path=tmp_path, case="ready", mode=mode, timeout=timeout)
    assert result.returncode == 2
    assert result.stderr
    assert commands == []
