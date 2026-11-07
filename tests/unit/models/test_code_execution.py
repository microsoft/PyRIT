# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Execution data is usable without scoring, messages, or memory."""

import pytest
from pydantic import TypeAdapter, ValidationError

from pyrit.models.code_execution import _ProgramExecutionEvent, _ProgramInvocation, _RuntimeProfileName, _SandboxRun


def _event(**updates: object) -> _ProgramExecutionEvent:
    return _ProgramExecutionEvent.model_validate(
        {
            "invocation_index": 0,
            "runtime_profile_name": "python-3.12",
            "resolved_command": ("python3", "-I", "/program/main.py"),
            "exit_code": 0,
            "duration_ms": 1,
            **updates,
        }
    )


def test_invocation_arguments_reject_nul() -> None:
    with pytest.raises(ValidationError):
        _ProgramInvocation(arguments=("a\x00b",))


@pytest.mark.parametrize("profile", ["", "-leading-dash", "has space", "image:tag", "a/b", "x" * 65])
def test_runtime_profile_name_rejects_invalid_names(profile: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(_RuntimeProfileName).validate_python(profile)


def test_timed_out_event_cannot_report_an_exit_code() -> None:
    with pytest.raises(ValidationError, match="timed-out"):
        _event(timed_out=True, exit_code=0)
    assert _event(timed_out=True, exit_code=None).exit_code is None


def test_runtime_image_provenance_is_optional_but_not_blank() -> None:
    assert _event().runtime_image_digest is None
    assert _event(runtime_image_digest="sha256:" + "a" * 64).runtime_image_digest == "sha256:" + "a" * 64
    with pytest.raises(ValidationError):
        _event(runtime_image_digest="")


def test_command_provenance_is_optional_but_not_empty() -> None:
    assert _event(resolved_command=None).resolved_command is None
    with pytest.raises(ValidationError):
        _event(resolved_command=())


@pytest.mark.parametrize(("stdout", "stderr"), [(False, False), (True, False), (False, True), (True, True)])
def test_output_truncation_reports_either_stream(stdout: bool, stderr: bool) -> None:
    assert _event(stdout_truncated=stdout, stderr_truncated=stderr).output_truncated is (stdout or stderr)


def test_invocation_index_is_nonnegative() -> None:
    with pytest.raises(ValidationError):
        _event(invocation_index=-1)


def test_sandbox_run_round_trips_shared_execution_data() -> None:
    run = _SandboxRun(
        events=(_event(stdout="result"),),
        reasons=("the backend stopped before invocation 1",),
        warnings=("cleanup was incomplete",),
    )
    adapter = TypeAdapter(_SandboxRun)
    serialized = adapter.dump_json(run)
    assert b'"invocation_index":0' in serialized
    assert b"case_index" not in serialized
    assert adapter.validate_json(serialized) == run
