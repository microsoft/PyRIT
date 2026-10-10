# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import io
import os
import pathlib
from types import SimpleNamespace
from unittest import mock

import dotenv
import pytest

from build_scripts.export_akv_environment import (
    DEFAULT_OUTPUT_FILE,
    _render,
    _serialize,
    _write_output,
    export_akv_environment,
)


@pytest.mark.parametrize(
    "value",
    [
        "single\\backslash",
        "double\\\\backslash",
        "four\\\\\\\\backslashes",
        "\\leading-and-trailing\\",
        r"C:\Users\name\secret.txt",
        "quote'\\${LITERAL}\nline\\two",
    ],
)
def test_serialize_round_trips_terminal_values(value: str) -> None:
    document = f"VALUE={_serialize(value)}\n"
    assert dotenv.dotenv_values(stream=io.StringIO(document), interpolate=True)["VALUE"] == value


def test_render_resolves_akv_only_values() -> None:
    document = (
        ("# AKV config\nBASE=bootstrap\nDERIVED=${BASE}\nAPI_KEY=kv:https://vault.vault.azure.net/secrets/api-key\n"),
        "https://vault.vault.azure.net",
    )
    client = mock.MagicMock()
    client.get_secret.return_value = SimpleNamespace(value="resolved-key")

    rendered = _render(
        document=document,
        credential=mock.MagicMock(),
        clients={document[1]: client},
        strict=True,
        silent=True,
    )

    values = dotenv.dotenv_values(stream=io.StringIO(rendered), interpolate=True)
    assert values == {"BASE": "bootstrap", "DERIVED": "bootstrap", "API_KEY": "resolved-key"}
    client.get_secret.assert_called_once_with("api-key", version=None)


@pytest.mark.parametrize(
    ("content", "expected_values", "expected_child_fetches"),
    [
        (
            "A=kv:https://vault.vault.azure.net/secrets/key\nB=${A}\nA=literal\n",
            {"A": "literal", "B": "resolved-key"},
            1,
        ),
        (
            "A=literal\nB=${A}\nA=kv:https://vault.vault.azure.net/secrets/key\n",
            {"A": "resolved-key", "B": "literal"},
            1,
        ),
        (
            "A=kv:https://vault.vault.azure.net/secrets/key\nB=${A}\nC=${B}\nB=literal\n",
            {"A": "resolved-key", "B": "literal", "C": "resolved-key"},
            2,
        ),
    ],
)
def test_render_resolves_interpolated_reference_assignments(
    content: str, expected_values: dict[str, str], expected_child_fetches: int
) -> None:
    document = (content, "https://vault.vault.azure.net")
    client = mock.MagicMock()
    client.get_secret.return_value = SimpleNamespace(value="resolved-key")

    rendered = _render(
        document=document,
        credential=mock.MagicMock(),
        clients={document[1]: client},
        strict=True,
        silent=True,
    )

    values = dict(dotenv.dotenv_values(stream=io.StringIO(rendered), interpolate=True))
    assert values == expected_values
    assert client.get_secret.call_args_list == [mock.call("key", version=None)] * expected_child_fetches


def test_render_non_strict_warns_and_skips_invalid_reference(caplog: pytest.LogCaptureFixture, capsys) -> None:
    document = ("GOOD=resolved\nBAD=kv:short-name\nOTHER=also-resolved", "https://vault.vault.azure.net")

    with caplog.at_level("WARNING", logger="build_scripts.export_akv_environment"):
        rendered = _render(
            document=document,
            credential=mock.MagicMock(),
            clients={},
            strict=False,
            silent=False,
        )

    assert "BAD=" not in rendered
    assert dotenv.dotenv_values(stream=io.StringIO(rendered)) == {
        "GOOD": "resolved",
        "OTHER": "also-resolved",
    }
    assert "WARNING: Invalid AKV reference for 'BAD' will be skipped" in capsys.readouterr().out
    assert "BAD" in caplog.text


def test_export_writes_env_akv_without_process_values(tmp_path: pathlib.Path) -> None:
    credential = mock.MagicMock()
    client = mock.MagicMock()
    client.get_secret.side_effect = [
        SimpleNamespace(value="VALUE=bootstrap\nKEY=kv:https://vault.vault.azure.net/secrets/key\n"),
        SimpleNamespace(value="resolved-key"),
    ]
    output_file = tmp_path / ".env_akv"

    with (
        mock.patch.dict(os.environ, {"PROCESS_ONLY": "not-written"}, clear=True),
        mock.patch("build_scripts.export_akv_environment._create_client", return_value=client),
    ):
        output = export_akv_environment(
            secret_urls=["https://vault.vault.azure.net/secrets/bootstrap"],
            output_file=output_file,
            credential=credential,
            silent=True,
        )

    assert output == output_file
    assert output.name == ".env_akv"
    assert DEFAULT_OUTPUT_FILE.name == ".env_akv"
    assert not (tmp_path / ".env").exists()
    assert dotenv.dotenv_values(dotenv_path=output_file) == {"VALUE": "bootstrap", "KEY": "resolved-key"}
    assert "PROCESS_ONLY" not in output_file.read_text(encoding="utf-8")
    client.close.assert_called_once()
    credential.close.assert_not_called()


def test_export_closes_client_but_not_provided_credential_on_failure(tmp_path: pathlib.Path) -> None:
    credential = mock.MagicMock()
    client = mock.MagicMock()
    client.get_secret.side_effect = RuntimeError("fetch failed")

    with (
        mock.patch("build_scripts.export_akv_environment._create_client", return_value=client),
        pytest.raises(RuntimeError, match="fetch failed"),
    ):
        export_akv_environment(
            secret_urls=["https://vault.vault.azure.net/secrets/bootstrap"],
            output_file=tmp_path / ".env_akv",
            credential=credential,
            silent=True,
        )

    client.close.assert_called_once()
    credential.close.assert_not_called()


def test_export_rejects_existing_output_before_fetch(tmp_path: pathlib.Path) -> None:
    output_file = tmp_path / ".env_akv"
    output_file.write_text("ORIGINAL=value\n", encoding="utf-8")

    with mock.patch("build_scripts.export_akv_environment._fetch_document") as mock_fetch:
        with pytest.raises(ValueError, match="already exists"):
            export_akv_environment(
                secret_urls=["https://vault.vault.azure.net/secrets/bootstrap"],
                output_file=output_file,
                credential=mock.MagicMock(),
                silent=True,
            )

    mock_fetch.assert_not_called()


def test_export_rejects_multiple_bootstrap_urls_before_fetch(tmp_path: pathlib.Path) -> None:
    with (
        mock.patch("build_scripts.export_akv_environment._fetch_document") as mock_fetch,
        pytest.raises(ValueError, match="Only one"),
    ):
        export_akv_environment(
            secret_urls=[
                "https://vault.vault.azure.net/secrets/first",
                "https://vault.vault.azure.net/secrets/second",
            ],
            output_file=tmp_path / ".env_akv",
            credential=mock.MagicMock(),
            silent=True,
        )

    mock_fetch.assert_not_called()


def test_write_output_does_not_clobber_existing_file(tmp_path: pathlib.Path) -> None:
    output_file = tmp_path / ".env_akv"
    output_file.write_text("ORIGINAL=value\n", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        _write_output(output_file=output_file, document="NEW=value\n")

    assert output_file.read_text(encoding="utf-8") == "ORIGINAL=value\n"
    assert list(tmp_path.glob(".env_akv.*.tmp")) == []


def test_write_output_secures_descriptor_before_writing(tmp_path: pathlib.Path) -> None:
    events: list[str] = []
    temporary_file = tmp_path / ".env_akv.test.tmp"
    stream = mock.MagicMock()
    stream.write.side_effect = lambda content: events.append(f"write:{content}")

    with (
        mock.patch(
            "build_scripts.export_akv_environment.tempfile.mkstemp",
            side_effect=lambda **kwargs: events.append("create") or (7, str(temporary_file)),
        ),
        mock.patch(
            "build_scripts.export_akv_environment.os.fchmod",
            side_effect=lambda *args: events.append("fchmod"),
            create=True,
        ),
        mock.patch(
            "build_scripts.export_akv_environment.os.fdopen",
            side_effect=lambda *args, **kwargs: events.append("fdopen") or stream,
        ),
        mock.patch(
            "build_scripts.export_akv_environment.os.link",
            side_effect=lambda *args: events.append("link"),
        ),
    ):
        _write_output(output_file=tmp_path / ".env_akv", document="VALUE=bootstrap\n")

    assert events == ["create", "fchmod", "fdopen", "write:VALUE=bootstrap\n", "link"]


def test_write_output_does_not_clobber_file_created_before_publish(tmp_path: pathlib.Path) -> None:
    output_file = tmp_path / ".env_akv"
    real_link = os.link

    def competing_link(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        output_file.write_text("CREATED_BY_OTHER_PROCESS=value\n", encoding="utf-8")
        real_link(source, destination)

    with (
        mock.patch("build_scripts.export_akv_environment.os.link", side_effect=competing_link),
        pytest.raises(ValueError, match="already exists"),
    ):
        _write_output(output_file=output_file, document="NEW=value\n")

    assert output_file.read_text(encoding="utf-8") == "CREATED_BY_OTHER_PROCESS=value\n"
    assert list(tmp_path.glob(".env_akv.*.tmp")) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits are not enforced on this platform.")
def test_write_output_uses_owner_only_permissions(tmp_path: pathlib.Path) -> None:
    configuration_directory = tmp_path / ".pyrit"
    output_file = _write_output(
        output_file=configuration_directory / ".env_akv",
        document="VALUE=bootstrap\n",
    )

    assert configuration_directory.stat().st_mode & 0o777 == 0o700
    assert output_file.stat().st_mode & 0o777 == 0o600


def test_write_output_rejects_symbolic_link(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "target"
    target.write_text("unchanged", encoding="utf-8")
    output_file = tmp_path / ".env_akv"
    try:
        output_file.symlink_to(target)
    except OSError:
        pytest.skip("Symbolic links are unavailable on this platform.")

    with pytest.raises(ValueError, match="symbolic link"):
        _write_output(output_file=output_file, document="VALUE=bootstrap\n")

    assert target.read_text(encoding="utf-8") == "unchanged"
