# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Personal-target credential commands in the thin CLI."""

import asyncio
import json
from argparse import Namespace
from collections.abc import Awaitable, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pyrit.cli import pyrit_scan
from pyrit.cli._auth import CliAuthenticationError
from pyrit.cli._target_credentials import (
    CredentialSource,
    CredentialSourceKind,
    TargetCredentialError,
    read_target_credential_async,
)
from pyrit.cli.api_client import PyRITApiClient
from pyrit.models import TargetCapabilities, TargetIdentifier
from pyrit.models.catalog import TargetInstance

_SECRET = "fake-secret-marker"
_ANT = CredentialSource(kind=CredentialSourceKind.ANT, ant_profile="copyrit")


def _target(*, name: str = "shared", personal: bool = False, configured: bool = False) -> TargetInstance:
    return TargetInstance(
        target_registry_name=name,
        identifier=TargetIdentifier(class_name="OpenAIChatTarget", class_module="pyrit.prompt_target"),
        capabilities=TargetCapabilities(),
        requires_caller_credential=personal,
        caller_credential_configured=configured,
    )


def _ant_process(*, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    process = MagicMock()
    process.communicate = AsyncMock(return_value=(stdout, stderr))
    process.returncode = returncode
    process.wait = AsyncMock()
    return process


@pytest.fixture
def ant_on_path() -> Iterator[None]:
    with patch("pyrit.cli._target_credentials.shutil.which", return_value="C:/tools/ant.exe"):
        yield


# ============================================================================
# Credential sources
# ============================================================================


async def test_prompt_reads_hidden_input() -> None:
    source = CredentialSource(kind=CredentialSourceKind.PROMPT)
    with (
        patch("pyrit.cli._target_credentials.sys.stdin.isatty", return_value=True),
        patch("pyrit.cli._target_credentials.getpass.getpass", return_value=f"  {_SECRET}\n") as prompt,
    ):
        assert await read_target_credential_async(source=source, target_name="mine") == _SECRET
    assert "mine" in prompt.call_args.args[0]


async def test_prompt_requires_an_interactive_terminal() -> None:
    source = CredentialSource(kind=CredentialSourceKind.PROMPT)
    with (
        patch("pyrit.cli._target_credentials.sys.stdin.isatty", return_value=False),
        pytest.raises(TargetCredentialError, match="interactive terminal"),
    ):
        await read_target_credential_async(source=source, target_name="mine")


@pytest.mark.parametrize(("environment", "match"), [({}, "is not set"), ({"MY_KEY": "  "}, "No credential")])
async def test_environment_source_requires_a_value(environment: dict[str, str], match: str) -> None:
    source = CredentialSource(kind=CredentialSourceKind.ENVIRONMENT, environment_variable="MY_KEY")
    with patch.dict("os.environ", environment, clear=True), pytest.raises(TargetCredentialError, match=match):
        await read_target_credential_async(source=source, target_name="mine")


async def test_environment_source_reads_the_variable() -> None:
    source = CredentialSource(kind=CredentialSourceKind.ENVIRONMENT, environment_variable="MY_KEY")
    with patch.dict("os.environ", {"MY_KEY": _SECRET}):
        assert await read_target_credential_async(source=source, target_name="mine") == _SECRET


@pytest.mark.usefixtures("ant_on_path")
async def test_ant_source_prints_the_profile_access_token(capsys: pytest.CaptureFixture[str]) -> None:
    process = _ant_process(stdout=f"{_SECRET}\n".encode())
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("pyrit.cli._target_credentials.asyncio.create_subprocess_exec", return_value=process) as run,
    ):
        assert await read_target_credential_async(source=_ANT, target_name="mine") == _SECRET

    assert run.call_args.args == (
        "C:/tools/ant.exe",
        "auth",
        "print-credentials",
        "--profile",
        "copyrit",
        "--access-token",
    )
    assert _SECRET not in capsys.readouterr().out


@pytest.mark.usefixtures("ant_on_path")
async def test_ant_source_uses_the_active_profile_by_default() -> None:
    process = _ant_process(stdout=_SECRET.encode())
    source = CredentialSource(kind=CredentialSourceKind.ANT)
    with patch("pyrit.cli._target_credentials.asyncio.create_subprocess_exec", return_value=process) as run:
        await read_target_credential_async(source=source, target_name="mine")

    assert "--profile" not in run.call_args.args


@pytest.mark.usefixtures("ant_on_path")
async def test_ant_source_warns_when_environment_credentials_take_precedence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    process = _ant_process(stdout=_SECRET.encode())
    with (
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "environment-key"}),
        patch("pyrit.cli._target_credentials.asyncio.create_subprocess_exec", return_value=process),
    ):
        await read_target_credential_async(source=_ANT, target_name="mine")

    error = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in error
    assert "environment-key" not in error


@pytest.mark.usefixtures("ant_on_path")
async def test_ant_source_reports_a_failed_login_without_its_output() -> None:
    process = _ant_process(stdout=_SECRET.encode(), stderr=b"not logged in\n", returncode=1)
    with (
        patch("pyrit.cli._target_credentials.asyncio.create_subprocess_exec", return_value=process),
        pytest.raises(TargetCredentialError) as error,
    ):
        await read_target_credential_async(source=_ANT, target_name="mine")

    assert "not logged in" in str(error.value)
    assert "ant auth login --profile copyrit" in str(error.value)
    assert _SECRET not in str(error.value)


@pytest.mark.usefixtures("ant_on_path")
async def test_ant_source_kills_a_process_that_does_not_finish() -> None:
    process = _ant_process()
    process.communicate = AsyncMock(side_effect=TimeoutError)
    with (
        patch("pyrit.cli._target_credentials.asyncio.create_subprocess_exec", return_value=process),
        pytest.raises(TargetCredentialError, match="in time"),
    ):
        await read_target_credential_async(source=_ANT, target_name="mine")

    process.kill.assert_called_once()


async def test_ant_source_requires_the_ant_cli() -> None:
    with (
        patch("pyrit.cli._target_credentials.shutil.which", return_value=None),
        pytest.raises(TargetCredentialError, match="not found on PATH"),
    ):
        await read_target_credential_async(source=_ANT, target_name="mine")


@pytest.mark.usefixtures("ant_on_path")
async def test_source_rejects_output_that_is_not_a_single_value() -> None:
    process = _ant_process(stdout=b"Active profile: copyrit\nsecret-value\n")
    with (
        patch("pyrit.cli._target_credentials.asyncio.create_subprocess_exec", return_value=process),
        pytest.raises(TargetCredentialError, match="not a single value") as error,
    ):
        await read_target_credential_async(source=_ANT, target_name="mine")

    assert "secret-value" not in str(error.value)


# ============================================================================
# API client
# ============================================================================


async def _with_transport_async(
    *,
    base_url: str,
    handler: Callable[[httpx.Request], httpx.Response],
    operation: Callable[[PyRITApiClient], Awaitable[object]],
) -> tuple[list[httpx.Request], object]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = PyRITApiClient(base_url=base_url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(record), base_url=base_url) as transport:
        client._client = transport
        result = await operation(client)
    return requests, result


async def test_client_sets_credential_in_the_request_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_target(name="my/target", personal=True).model_dump(mode="json"))

    requests, result = await _with_transport_async(
        base_url="http://localhost:8000",
        handler=handler,
        operation=lambda client: client.set_target_credential_async(
            target_registry_name="my/target", credential=_SECRET
        ),
    )

    assert requests[0].method == "PUT"
    assert requests[0].url.raw_path == b"/api/targets/my%2Ftarget/credentials"
    assert json.loads(requests[0].content) == {"credential": _SECRET}
    assert _SECRET not in str(requests[0].url)
    assert isinstance(result, TargetInstance)


async def test_client_refuses_to_send_credentials_to_a_remote_http_backend() -> None:
    handler = MagicMock()

    with pytest.raises(CliAuthenticationError, match="non-HTTPS"):
        await _with_transport_async(
            base_url="http://remote.example:8000",
            handler=handler,
            operation=lambda client: client.set_target_credential_async(
                target_registry_name="mine", credential=_SECRET
            ),
        )
    with pytest.raises(CliAuthenticationError, match="non-HTTPS"):
        await _with_transport_async(
            base_url="http://remote.example:8000",
            handler=handler,
            operation=lambda client: client.create_personal_target_async(
                name="mine", source=_target(), credential=_SECRET
            ),
        )
    handler.assert_not_called()


async def test_client_creates_a_personal_copy_of_a_source_target() -> None:
    source = _target(name="claude_shared")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=_target(name="mine", personal=True).model_dump(mode="json"))

    requests, _ = await _with_transport_async(
        base_url="https://pyrit.example",
        handler=handler,
        operation=lambda client: client.create_personal_target_async(
            name="mine", source=source, params={"temperature": "0.3"}, credential=_SECRET
        ),
    )

    assert json.loads(requests[0].content) == {
        "name": "mine",
        "type": "OpenAIChatTarget",
        "requires_caller_credential": True,
        "source": {
            "source_name": "claude_shared",
            "source_hash": source.identifier.hash,
            "params": {"temperature": "0.3", "api_key": _SECRET},
        },
    }


async def test_client_reports_missing_targets_and_removes_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if request.method == "GET" else 204)

    requests, missing = await _with_transport_async(
        base_url="http://localhost:8000",
        handler=handler,
        operation=lambda client: client.get_target_async(target_registry_name="missing"),
    )
    assert missing is None

    requests, _ = await _with_transport_async(
        base_url="http://localhost:8000",
        handler=handler,
        operation=lambda client: client.remove_target_credential_async(target_registry_name="mine"),
    )
    assert (requests[0].method, requests[0].url.path) == ("DELETE", "/api/targets/mine/credentials")


# ============================================================================
# Verbs
# ============================================================================


def _parse(*argv: str) -> Namespace:
    return pyrit_scan._build_parser().parse_args(list(argv))


def _cli_client(*, target: TargetInstance | None) -> MagicMock:
    client = MagicMock(spec=PyRITApiClient)
    client.get_target_async = AsyncMock(return_value=target)
    client.set_target_credential_async = AsyncMock(return_value=target)
    client.remove_target_credential_async = AsyncMock()
    client.create_personal_target_async = AsyncMock(return_value=_target(name="mine", personal=True))
    return client


def test_parser_registers_credential_verbs() -> None:
    parsed = _parse("set-target-credential", "mine", "--from-ant", "--ant-profile", "copyrit", "--keep-fresh")
    assert (parsed.command, parsed.target, parsed.from_ant, parsed.ant_profile, parsed.keep_fresh) == (
        "set-target-credential",
        "mine",
        True,
        "copyrit",
        True,
    )
    assert parsed.refresh_minutes == pyrit_scan._DEFAULT_REFRESH_MINUTES
    parsed = _parse("add-personal-target", "mine", "--from", "shared", "--set", "temperature=0.3")
    assert (parsed.source_target, parsed.target_overrides) == ("shared", [("temperature", "0.3")])
    assert _parse("remove-target-credential", "mine").command == "remove-target-credential"


@pytest.mark.parametrize(
    "argv",
    [
        ("set-target-credential", "mine"),
        ("set-target-credential", "mine", "--prompt", "--from-env", "MY_KEY"),
        ("add-personal-target", "mine", "--from", "shared", "--set", "api_key=value"),
        ("add-personal-target", "mine", "--from", "shared", "--set", "missing-separator"),
        ("add-personal-target", "mine"),
    ],
)
def test_parser_rejects_invalid_credential_arguments(argv: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit):
        _parse(*argv)


@pytest.mark.parametrize(
    ("argv", "match"),
    [
        (("--prompt", "--ant-profile", "copyrit"), "--ant-profile requires --from-ant"),
        (("--from-env", "MY_KEY", "--keep-fresh"), "--keep-fresh requires --from-ant"),
    ],
)
async def test_set_credential_rejects_options_for_another_source(
    argv: tuple[str, ...], match: str, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _cli_client(target=_target(personal=True))

    exit_code = await pyrit_scan._dispatch_with_client_async(
        client=client, parsed_args=_parse("set-target-credential", "mine", *argv)
    )

    assert exit_code == 1
    assert match in capsys.readouterr().out
    client.set_target_credential_async.assert_not_awaited()


async def test_set_credential_sends_the_sources_value(capsys: pytest.CaptureFixture[str]) -> None:
    client = _cli_client(target=_target(name="mine", personal=True))

    with patch.dict("os.environ", {"MY_KEY": _SECRET}):
        exit_code = await pyrit_scan._dispatch_with_client_async(
            client=client, parsed_args=_parse("set-target-credential", "mine", "--from-env", "MY_KEY")
        )

    assert exit_code == 0
    assert client.set_target_credential_async.call_args.kwargs == {
        "target_registry_name": "mine",
        "credential": _SECRET,
    }
    output = capsys.readouterr().out
    assert "environment variable MY_KEY" in output
    assert _SECRET not in output


@pytest.mark.parametrize(("target", "message"), [(None, "not found"), (_target(name="mine"), "add-personal-target")])
async def test_set_credential_requires_a_personal_target(
    target: TargetInstance | None, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _cli_client(target=target)

    with patch.dict("os.environ", {"MY_KEY": _SECRET}):
        exit_code = await pyrit_scan._dispatch_with_client_async(
            client=client, parsed_args=_parse("set-target-credential", "mine", "--from-env", "MY_KEY")
        )

    assert exit_code == 1
    assert message in capsys.readouterr().out
    client.set_target_credential_async.assert_not_awaited()


async def test_keep_fresh_resends_until_stopped_and_survives_failures(capsys: pytest.CaptureFixture[str]) -> None:
    client = _cli_client(target=_target(name="mine", personal=True))
    client.set_target_credential_async = AsyncMock(
        side_effect=[None, httpx.ConnectError("backend restarting"), None, None]
    )
    sleeps = AsyncMock(side_effect=[None, None, asyncio.CancelledError])
    tokens = AsyncMock(side_effect=["first", "second", "third"])

    with (
        patch("pyrit.cli.pyrit_scan.asyncio.sleep", sleeps),
        patch("pyrit.cli._target_credentials.read_target_credential_async", tokens),
    ):
        exit_code = await pyrit_scan._dispatch_with_client_async(
            client=client,
            parsed_args=_parse("set-target-credential", "mine", "--from-ant", "--keep-fresh", "--refresh-minutes", "2"),
        )

    assert exit_code == 0
    assert sleeps.await_args_list[0].args == (120,)
    assert [call.kwargs["credential"] for call in client.set_target_credential_async.await_args_list] == [
        "first",
        "second",
        "third",
    ]
    captured = capsys.readouterr()
    assert "could not refresh" in captured.err
    assert "Refreshed your credential" in captured.out
    assert "Stopped refreshing" in captured.out


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("PUT", "http://localhost:8000/api/targets/mine/credentials")
    response = httpx.Response(status_code, request=request, json={"detail": "server detail"})
    return httpx.HTTPStatusError(f"{status_code}: server detail", request=request, response=response)


async def _keep_fresh_async(client: MagicMock, *, sleeps: AsyncMock) -> int:
    with (
        patch("pyrit.cli.pyrit_scan.asyncio.sleep", sleeps),
        patch("pyrit.cli._target_credentials.read_target_credential_async", AsyncMock(return_value=_SECRET)),
    ):
        return await pyrit_scan._dispatch_with_client_async(
            client=client, parsed_args=_parse("set-target-credential", "mine", "--from-ant", "--keep-fresh")
        )


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
async def test_keep_fresh_stops_on_a_permanent_error(capsys: pytest.CaptureFixture[str], status_code: int) -> None:
    client = _cli_client(target=_target(name="mine", personal=True))
    client.set_target_credential_async = AsyncMock(side_effect=[None, _status_error(status_code)])
    sleeps = AsyncMock(side_effect=[None, None, asyncio.CancelledError])

    exit_code = await _keep_fresh_async(client, sleeps=sleeps)

    assert exit_code == 1
    assert client.set_target_credential_async.await_count == 2
    assert sleeps.await_count == 1
    captured = capsys.readouterr()
    assert "server detail" in captured.err
    assert ("add-personal-target mine" in captured.err) is (status_code == 404)
    assert _SECRET not in captured.out + captured.err


@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_keep_fresh_retries_a_temporary_error(capsys: pytest.CaptureFixture[str], status_code: int) -> None:
    client = _cli_client(target=_target(name="mine", personal=True))
    client.set_target_credential_async = AsyncMock(side_effect=[None, _status_error(status_code), None])
    sleeps = AsyncMock(side_effect=[None, None, asyncio.CancelledError])

    exit_code = await _keep_fresh_async(client, sleeps=sleeps)

    assert exit_code == 0
    assert client.set_target_credential_async.await_count == 3
    captured = capsys.readouterr()
    assert "Warning: could not refresh" in captured.err
    assert "Refreshed your credential" in captured.out


@pytest.mark.parametrize(
    "current",
    [
        None,
        TargetInstance(
            target_registry_name="mine",
            identifier=TargetIdentifier(
                class_name="OpenAIChatTarget", class_module="pyrit.prompt_target", endpoint="https://other.test/v1"
            ),
            capabilities=TargetCapabilities(),
            requires_caller_credential=True,
        ),
    ],
)
async def test_keep_fresh_stops_when_the_target_is_gone_or_replaced(
    capsys: pytest.CaptureFixture[str], current: TargetInstance | None
) -> None:
    client = _cli_client(target=_target(name="mine", personal=True))
    client.get_target_async = AsyncMock(side_effect=[_target(name="mine", personal=True), current])
    sleeps = AsyncMock(side_effect=[None, asyncio.CancelledError])

    exit_code = await _keep_fresh_async(client, sleeps=sleeps)

    assert exit_code == 1
    assert client.set_target_credential_async.await_count == 1
    assert "no longer the target you set your credential for" in capsys.readouterr().err


async def test_add_personal_target_copies_the_source_with_overrides_and_credential(
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _target(name="claude_shared")
    client = _cli_client(target=source)

    with patch("pyrit.cli._target_credentials.read_target_credential_async", AsyncMock(return_value=_SECRET)) as read:
        exit_code = await pyrit_scan._dispatch_with_client_async(
            client=client,
            parsed_args=_parse(
                "add-personal-target",
                "mine",
                "--from",
                "claude_shared",
                "--set",
                "temperature=0.3",
                "--from-ant",
                "--ant-profile",
                "copyrit",
            ),
        )

    assert exit_code == 0
    assert read.call_args.kwargs["source"] == _ANT
    assert client.create_personal_target_async.call_args.kwargs == {
        "name": "mine",
        "source": source,
        "params": {"temperature": "0.3"},
        "credential": _SECRET,
    }
    output = capsys.readouterr().out
    assert "Registered personal target 'mine'" in output
    assert _SECRET not in output


async def test_add_personal_target_without_a_source_explains_how_to_set_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _cli_client(target=_target(name="shared"))

    exit_code = await pyrit_scan._dispatch_with_client_async(
        client=client, parsed_args=_parse("add-personal-target", "mine", "--from", "shared")
    )

    assert exit_code == 0
    assert client.create_personal_target_async.call_args.kwargs["credential"] is None
    assert "set-target-credential mine" in capsys.readouterr().out


async def test_add_personal_target_rejects_duplicate_overrides(capsys: pytest.CaptureFixture[str]) -> None:
    client = _cli_client(target=_target(name="shared"))

    exit_code = await pyrit_scan._dispatch_with_client_async(
        client=client,
        parsed_args=_parse(
            "add-personal-target", "mine", "--from", "shared", "--set", "temperature=1", "temperature=2"
        ),
    )

    assert exit_code == 1
    assert "more than once" in capsys.readouterr().out
    client.create_personal_target_async.assert_not_awaited()


async def test_remove_credential_calls_the_backend() -> None:
    client = _cli_client(target=_target(name="mine", personal=True))

    exit_code = await pyrit_scan._dispatch_with_client_async(
        client=client, parsed_args=_parse("remove-target-credential", "mine")
    )

    assert exit_code == 0
    client.remove_target_credential_async.assert_awaited_once_with(target_registry_name="mine")
