# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Whether the provider rejected a target's credentials on the last request through it."""

import asyncio

import httpx
import openai
import pytest
from unit.async_utils import TEST_WATCHDOG_SECONDS
from unit.mocks import MockPromptTarget

from pyrit.exceptions import PyritException
from pyrit.models import Message, MessagePiece


class _FailingTarget(MockPromptTarget):
    error: Exception | None = None

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        if self.error is not None:
            raise self.error
        return await super()._send_prompt_to_target_async(normalized_conversation=normalized_conversation)


def _status_error(error_type: type[openai.APIStatusError], status_code: int) -> openai.APIStatusError:
    request = httpx.Request("POST", "https://api.example.test/v1/chat/completions")
    return error_type("rejected", response=httpx.Response(status_code, request=request), body=None)


def _translated_auth_error() -> PyritException:
    try:
        raise _status_error(openai.AuthenticationError, 401)
    except openai.AuthenticationError as exc:
        try:
            raise PyritException(message="Authentication failed") from exc
        except PyritException as translated:
            return translated


def _message() -> Message:
    return MessagePiece(role="user", original_value="hi").to_message()


async def _send_async(target: _FailingTarget, error: Exception | None) -> None:
    target.error = error
    if error is None:
        await target.send_prompt_async(message=_message())
    else:
        with pytest.raises(type(error)):
            await target.send_prompt_async(message=_message())


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "error",
    [
        _status_error(openai.AuthenticationError, 401),
        _status_error(openai.PermissionDeniedError, 403),
        PyritException(status_code=401, message="Authentication failed"),
        _translated_auth_error(),
    ],
)
async def test_an_authentication_failure_marks_credentials_rejected_until_a_request_succeeds(error: Exception) -> None:
    target = _FailingTarget()
    before = target.credentials_rejected

    await _send_async(target, error)
    after_rejection = target.credentials_rejected

    await _send_async(target, None)
    after_success = target.credentials_rejected
    assert (before, after_rejection, after_success) == (None, True, False)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "error", [_status_error(openai.InternalServerError, 500), PyritException(status_code=429), ValueError("bad")]
)
async def test_other_failures_leave_the_credential_status_unchanged(error: Exception) -> None:
    target = _FailingTarget()
    await _send_async(target, _status_error(openai.AuthenticationError, 401))

    await _send_async(target, error)

    assert target.credentials_rejected


@pytest.mark.usefixtures("patch_central_database")
async def test_a_copy_reports_into_its_source() -> None:
    source = _FailingTarget()
    copy = _FailingTarget()
    copy.attach_reconstruction_source(source)

    await _send_async(copy, _status_error(openai.AuthenticationError, 401))

    assert source.credentials_rejected


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://ml.example.test/score")
    return httpx.HTTPStatusError("rejected", request=request, response=httpx.Response(status_code, request=request))


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("status_code", [401, 403])
async def test_an_http_status_error_from_the_provider_marks_credentials_rejected(status_code: int) -> None:
    target = _FailingTarget()

    await _send_async(target, _http_status_error(status_code))

    assert target.credentials_rejected is True


@pytest.mark.usefixtures("patch_central_database")
async def test_the_credential_status_is_unknown_until_a_request_completes() -> None:
    target = _FailingTarget()
    assert target.credentials_rejected is None

    await _send_async(target, ValueError("bad"))
    assert target.credentials_rejected is None

    await _send_async(target, None)
    assert target.credentials_rejected is False


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("reject", [False, True])
async def test_a_request_in_flight_at_rotation_does_not_report_status_for_the_new_key_async(reject: bool) -> None:
    started = asyncio.Event()
    finish = asyncio.Event()

    class _PausedTarget(_FailingTarget):
        async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
            started.set()
            await finish.wait()
            return await super()._send_prompt_to_target_async(normalized_conversation=normalized_conversation)

    target = _PausedTarget()
    async with asyncio.TaskGroup() as group:
        group.create_task(_send_async(target, _http_status_error(401) if reject else None))
        await asyncio.wait_for(started.wait(), timeout=TEST_WATCHDOG_SECONDS)
        target._reset_credential_status()
        finish.set()

    assert target.credentials_rejected is None


@pytest.mark.usefixtures("patch_central_database")
async def test_a_copy_made_before_rotation_does_not_report_status_for_the_new_key_async() -> None:
    source = _FailingTarget()
    old_copy = _FailingTarget()
    old_copy.attach_reconstruction_source(source)
    source._reset_credential_status()

    await _send_async(old_copy, _http_status_error(401))
    after_old_copy = source.credentials_rejected
    new_copy = _FailingTarget()
    new_copy.attach_reconstruction_source(source)
    await _send_async(new_copy, None)

    assert (after_old_copy, source.credentials_rejected) == (None, False)
