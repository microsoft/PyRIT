# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import pathlib
import tempfile
import types
import warnings
from unittest import mock

import pytest
from azure.core.exceptions import ResourceNotFoundError
from dotenv import dotenv_values

from pyrit.exceptions import KeyVaultInitializationException
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.akv_initialization import (
    _load_env_from_akv_async,
    _load_environment_async,
    _load_environment_files,
    _parse_akv_reference,
    _parse_akv_secret_url,
    _warn_about_akv_environment_files,
    _write_akv_env_file,
)


class TestLoadEnvironmentFiles:
    """Tests for _load_environment_files function and env_files parameter in initialize_pyrit_async."""

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_loads_default_env_files_when_none_provided(self, mock_config_path):
        """Test that default .env and .env.local files are loaded when env_files is None."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_local_file = temp_path / ".env.local"
            env_file.write_text("VAR1=value1")
            env_local_file.write_text("VAR2=value2")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                pytest.warns(DeprecationWarning, match=r"removed in 1\.3\.0"),
            ):
                loaded = _load_environment_files(env_files=None)

                assert loaded is True
                assert os.environ["VAR1"] == "value1"
                assert os.environ["VAR2"] == "value2"

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_only_loads_existing_default_files(self, mock_config_path):
        """Test that only existing default files are loaded."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_file.write_text("VAR1=value1")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                pytest.warns(DeprecationWarning, match=r"removed in 1\.3\.0"),
            ):
                loaded = _load_environment_files(env_files=None)

                assert loaded is True
                assert os.environ["VAR1"] == "value1"

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_default_env_preserves_process_environment(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env").write_text("VAR=legacy\nLEGACY_ONLY=legacy")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with (
                mock.patch.dict(os.environ, {"VAR": "process"}, clear=True),
                pytest.warns(DeprecationWarning, match=r"removed in 1\.3\.0"),
            ):
                loaded = _load_environment_files(env_files=None, silent=True)

                assert loaded is True
                assert os.environ["VAR"] == "process"
                assert os.environ["LEGACY_ONLY"] == "legacy"

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_default_env_local_overrides_process_environment_and_env(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env").write_text("VAR=legacy")
            (temp_path / ".env.local").write_text("VAR=local")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with (
                mock.patch.dict(os.environ, {"VAR": "process"}, clear=True),
                pytest.warns(DeprecationWarning, match=r"removed in 1\.3\.0"),
            ):
                loaded = _load_environment_files(env_files=None, silent=True)

                assert loaded is True
                assert os.environ["VAR"] == "local"

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_excludes_default_env_when_loading_local_override(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_local_file = temp_path / ".env.local"
            env_file.write_text("VAR=base")
            env_local_file.write_text("VAR=local")

            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = _load_environment_files(env_files=None, include_default_base=False)

                assert loaded is True
                assert os.environ["VAR"] == "local"

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_returns_false_when_no_default_files_exist(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = _load_environment_files(env_files=None)

                assert loaded is False
                assert os.environ == {}

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    def test_auto_discovered_env_warns_with_removal_version(self, mock_config_path, caplog, capsys):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_file.write_text("VAR=base")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with (
                caplog.at_level("WARNING", logger="pyrit.setup.akv_initialization"),
                pytest.warns(DeprecationWarning, match=r"\.env.*removed in 1\.3\.0.*\.env\.local"),
            ):
                _load_environment_files(env_files=None)

        output = capsys.readouterr().out
        assert f"WARNING: Auto-discovered {env_file} is deprecated" in output
        assert "Use env_akv_ref or ~/.pyrit/.env.local instead" in output
        assert f"Auto-discovered {env_file} is deprecated" in caplog.text

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    def test_explicit_env_file_does_not_emit_legacy_deprecation(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            explicit_env = temp_path / ".env"
            explicit_env.write_text("VAR=explicit")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                loaded = _load_environment_files(env_files=[explicit_env], silent=True)

        assert loaded is True

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    def test_akv_legacy_env_warning_respects_silent(self, mock_config_path, caplog, capsys):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env").write_text("VAR=base")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with (
                caplog.at_level("WARNING", logger="pyrit.setup.akv_initialization"),
                pytest.warns(DeprecationWarning, match=r"removed in 1\.3\.0"),
            ):
                _warn_about_akv_environment_files(env_files=None, silent=True)

        assert capsys.readouterr().out == ""
        assert "will be ignored because env_akv_ref is configured" in caplog.text

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_akv_ignores_auto_discovered_env_and_loads_env_local(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env").write_text("VALUE=legacy")
            (temp_path / ".env.local").write_text("VALUE=local")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with (
                mock.patch(
                    "pyrit.setup.akv_initialization._load_env_from_akv_async",
                    new_callable=mock.AsyncMock,
                    return_value="VALUE=akv\n",
                ),
                mock.patch("pyrit.setup.akv_initialization._load_environment_files") as mock_load_files,
                pytest.warns(DeprecationWarning, match=r"removed in 1\.3\.0"),
            ):
                await _load_environment_async(
                    env_akv_ref=["https://vault.vault.azure.net/secrets/bootstrap"],
                    env_files=None,
                    env_akv_strict=True,
                    env_akv_write_env=False,
                    silent=True,
                )

            assert mock_load_files.call_args.kwargs["env_files"] is None
            assert mock_load_files.call_args.kwargs["include_default_base"] is False

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_akv_debug_mode_rejects_existing_env_before_fetch(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_file.write_text("VALUE=legacy")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with (
                mock.patch(
                    "pyrit.setup.akv_initialization._load_env_from_akv_async", new_callable=mock.AsyncMock
                ) as mock_load_akv,
                pytest.raises(ValueError, match=r"already exists.*rename or remove"),
            ):
                await _load_environment_async(
                    env_akv_ref=["https://vault.vault.azure.net/secrets/bootstrap"],
                    env_files=None,
                    env_akv_strict=True,
                    env_akv_write_env=True,
                    silent=True,
                )

            mock_load_akv.assert_not_awaited()

    async def test_loads_custom_env_files_in_order(self):
        """Test that custom env_files are loaded in the order provided."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env1 = temp_path / ".env.test"
            env2 = temp_path / ".env.prod"
            env3 = temp_path / ".env.local"

            # Create files
            env1.write_text("VAR=test")
            env2.write_text("VAR=prod")
            env3.write_text("VAR=local")

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = _load_environment_files(env_files=[env1, env2, env3])

                assert loaded is True
                assert os.environ["VAR"] == "local"

    async def test_explicit_files_only_override_when_named_env_local(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            first_file = temp_path / "first.env"
            second_file = temp_path / "second.env"
            local_file = temp_path / "nested" / ".env.local"
            local_file.parent.mkdir()
            first_file.write_text("PROCESS_VALUE=first\nFILE_VALUE=first")
            second_file.write_text("PROCESS_VALUE=second\nFILE_VALUE=second\nSECOND_ONLY=second")
            local_file.write_text("PROCESS_VALUE=local\nFILE_VALUE=local")

            with mock.patch.dict(os.environ, {"PROCESS_VALUE": "process"}, clear=True):
                loaded = _load_environment_files(env_files=[first_file, second_file, local_file], silent=True)

                assert loaded is True
                assert os.environ["PROCESS_VALUE"] == "local"
                assert os.environ["FILE_VALUE"] == "local"
                assert os.environ["SECOND_ONLY"] == "second"

            with mock.patch.dict(os.environ, {"PROCESS_VALUE": "process"}, clear=True):
                _load_environment_files(env_files=[first_file, second_file], silent=True)

                assert os.environ["PROCESS_VALUE"] == "process"
                assert os.environ["FILE_VALUE"] == "first"

    async def test_load_environment_files_interpolates_in_assignment_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / ".env"
            env_file.write_text("A=one\nB=${A}\nA=two\nC=${A}")

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = _load_environment_files(env_files=[env_file], silent=True)

                assert loaded is True
                assert os.environ["A"] == "two"
                assert os.environ["B"] == "one"
                assert os.environ["C"] == "two"

    async def test_load_environment_files_honors_python_dotenv_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / ".env"
            env_file.write_text("DISABLED_VALUE=not-loaded")

            with mock.patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "true"}, clear=True):
                loaded = _load_environment_files(env_files=[env_file], silent=True)

                assert loaded is True
                assert "DISABLED_VALUE" not in os.environ

    async def test_load_environment_async_write_env_writes_resolved_native_bootstrap(self):
        credential, client = _create_mock_akv_clients()
        document = (
            "# Bootstrap values\n"
            "BASE=bootstrap\n"
            "DERIVED=${BASE}\n"
            "API_KEY=kv:https://vault.vault.azure.net/secrets/api-key\n"
        )
        resolved_api_key = "line one\nquote' and literal ${UNRELATED}"
        client.get_secret = mock.AsyncMock(
            side_effect=[
                types.SimpleNamespace(value=document),
                types.SimpleNamespace(value=resolved_api_key),
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env.local").write_text("API_KEY=local-key\nLOCAL_ONLY=local", encoding="utf-8")
            with (
                mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH", temp_path),
                mock.patch.dict(
                    os.environ,
                    {
                        "BASE": "process",
                        "API_KEY": "process-key",
                        "PROCESS_ONLY": "not-written",
                        "UNRELATED": "changed",
                    },
                    clear=True,
                ),
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            ):
                await _load_environment_async(
                    env_akv_ref=["https://vault.vault.azure.net/secrets/bootstrap"],
                    env_files=None,
                    env_akv_strict=True,
                    env_akv_write_env=True,
                    silent=True,
                )

                assert os.environ["BASE"] == "process"
                assert os.environ["API_KEY"] == "local-key"
                assert os.environ["LOCAL_ONLY"] == "local"

            written_env = temp_path / ".env"
            assert written_env.is_file()
            assert not (temp_path / ".env.new").exists()
            content = written_env.read_text(encoding="utf-8")
            assert "# Bootstrap values" in content
            assert "kv:" not in content
            assert "PROCESS_ONLY" not in content
            assert "LOCAL_ONLY" not in content

            with mock.patch.dict(os.environ, {}, clear=True):
                written_values = dotenv_values(dotenv_path=written_env, interpolate=True)

            assert written_values == {
                "BASE": "bootstrap",
                "DERIVED": "bootstrap",
                "API_KEY": resolved_api_key,
            }
            assert client.get_secret.await_args_list == [
                mock.call("bootstrap", version=None),
                mock.call("api-key", version=None),
            ]

    async def test_load_environment_async_write_env_filters_generated_explicit_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            generated_env = temp_path / ".env"
            local_env = temp_path / ".env.local"
            local_env.write_text("LOCAL=value", encoding="utf-8")

            with (
                mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH", temp_path),
                mock.patch(
                    "pyrit.setup.akv_initialization._load_env_from_akv_async",
                    new_callable=mock.AsyncMock,
                    return_value="VALUE=bootstrap\n",
                ),
                mock.patch(
                    "pyrit.setup.akv_initialization._load_environment_files", return_value=True
                ) as mock_load_environment_files,
            ):
                await _load_environment_async(
                    env_akv_ref=["https://vault.vault.azure.net/secrets/bootstrap"],
                    env_files=[generated_env, local_env],
                    env_akv_strict=True,
                    env_akv_write_env=True,
                    silent=True,
                )

            assert mock_load_environment_files.call_args.kwargs["env_files"] == [local_env]
            assert mock_load_environment_files.call_args.kwargs["include_default_base"] is True

    async def test_load_environment_async_write_env_preserves_first_bootstrap_value(self):
        documents = [
            "SHARED=first\nFIRST_ONLY=first\n",
            "SHARED=second\nSECOND_ONLY=second\n",
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            with (
                mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH", temp_path),
                mock.patch(
                    "pyrit.setup.akv_initialization._load_env_from_akv_async",
                    new_callable=mock.AsyncMock,
                    side_effect=lambda **kwargs: documents.pop(0),
                ),
            ):
                await _load_environment_async(
                    env_akv_ref=[
                        "https://vault.vault.azure.net/secrets/first",
                        "https://vault.vault.azure.net/secrets/second",
                    ],
                    env_files=[],
                    env_akv_strict=True,
                    env_akv_write_env=True,
                    silent=True,
                )

            with mock.patch.dict(os.environ, {}, clear=True):
                written_values = dotenv_values(dotenv_path=temp_path / ".env", interpolate=True)

            assert written_values == {
                "SHARED": "first",
                "FIRST_ONLY": "first",
                "SECOND_ONLY": "second",
            }

    def test_write_akv_env_file_secures_descriptor_before_writing(self):
        events: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            temporary_file = temp_path / ".env.test.tmp"
            stream = mock.MagicMock()
            stream.write.side_effect = lambda content: events.append(f"write:{content}")
            with (
                mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH", temp_path),
                mock.patch(
                    "pyrit.setup.akv_initialization.tempfile.mkstemp",
                    side_effect=lambda **kwargs: events.append("create") or (7, str(temporary_file)),
                ),
                mock.patch(
                    "pyrit.setup.akv_initialization.os.fchmod",
                    side_effect=lambda *args: events.append("fchmod"),
                    create=True,
                ),
                mock.patch(
                    "pyrit.setup.akv_initialization.os.fdopen",
                    side_effect=lambda *args, **kwargs: events.append("fdopen") or stream,
                ),
                mock.patch(
                    "pyrit.setup.akv_initialization.os.replace",
                    side_effect=lambda *args: events.append("replace"),
                ),
            ):
                _write_akv_env_file(documents=["VALUE=bootstrap\n"], silent=True)

        assert events == ["create", "fchmod", "fdopen", "write:VALUE=bootstrap\n", "replace"]

    def test_write_akv_env_file_preserves_existing_file_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_file.write_text("ORIGINAL=value\n", encoding="utf-8")

            with (
                mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH", temp_path),
                mock.patch("pyrit.setup.akv_initialization.os.replace", side_effect=OSError("replace failed")),
                pytest.raises(OSError, match="replace failed"),
            ):
                _write_akv_env_file(documents=["NEW=value\n"], silent=True)

            assert env_file.read_text(encoding="utf-8") == "ORIGINAL=value\n"
            assert list(temp_path.glob(".env.*.tmp")) == []

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits are not enforced on this platform.")
    def test_write_akv_env_file_uses_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            configuration_directory = pathlib.Path(temp_dir) / ".pyrit"
            with mock.patch(
                "pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH", configuration_directory
            ):
                env_file = _write_akv_env_file(documents=["VALUE=bootstrap\n"], silent=True)

            assert configuration_directory.stat().st_mode & 0o777 == 0o700
            assert env_file.stat().st_mode & 0o777 == 0o600

    def test_write_akv_env_file_rejects_symbolic_link(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            target = temp_path / "target"
            target.write_text("unchanged", encoding="utf-8")
            env_file = temp_path / ".env"
            try:
                env_file.symlink_to(target)
            except OSError:
                pytest.skip("Symbolic links are unavailable on this platform.")

            with (
                mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH", temp_path),
                pytest.raises(ValueError, match="symbolic link"),
            ):
                _write_akv_env_file(documents=["VALUE=bootstrap\n"], silent=True)

            assert target.read_text(encoding="utf-8") == "unchanged"

    async def test_direct_local_file_loader_keeps_pyrit_references_literal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / ".env"
            env_file.write_text(
                "BASE_VALUE=base\nKV_REFERENCE=kv:api-key\nENV_REFERENCE=env:SOURCE_VALUE\nINTERPOLATED=${BASE_VALUE}"
            )

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = _load_environment_files(env_files=[env_file], silent=True)

                assert loaded is True
                assert os.environ["KV_REFERENCE"] == "kv:api-key"
                assert os.environ["ENV_REFERENCE"] == "env:SOURCE_VALUE"
                assert os.environ["INTERPOLATED"] == "base"

    @mock.patch("pyrit.setup.akv_initialization.path.CONFIGURATION_DIRECTORY_PATH")
    def test_default_local_file_can_interpolate_base_file_but_not_reverse(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_local_file = temp_path / ".env.local"
            env_file.write_text(
                "OPENAI_CHAT_ENDPOINT=https://example.openai.azure.com/openai/v1\nFROM_LATER_LOCAL=${LOCAL_ONLY}"
            )
            env_local_file.write_text("FOOBAR=${OPENAI_CHAT_ENDPOINT}\nLOCAL_ONLY=local")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                pytest.warns(DeprecationWarning, match=r"removed in 1\.3\.0"),
            ):
                loaded = _load_environment_files(env_files=None, silent=True)

                assert loaded is True
                assert os.environ["FOOBAR"] == "https://example.openai.azure.com/openai/v1"
                assert os.environ["FROM_LATER_LOCAL"] == ""
                assert os.environ["LOCAL_ONLY"] == "local"

    async def test_env_akv_strict_does_not_validate_local_environment_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / ".env"
            env_file.write_text("GOOD=resolved\n=malformed\nOTHER=also-resolved")

            with mock.patch.dict(os.environ, {}, clear=True):
                await initialize_pyrit_async(
                    memory_db_type=IN_MEMORY,
                    env_files=[env_file],
                    env_akv_strict=True,
                    load_defaults=False,
                    silent=True,
                )

                assert os.environ["GOOD"] == "resolved"
                assert os.environ["OTHER"] == "also-resolved"

    @pytest.mark.parametrize(
        ("file_name", "initial_environment"),
        [
            ("custom.env", {}),
            (".env.local", {"API_KEY": "process-key"}),
        ],
    )
    async def test_load_environment_async_resolves_local_akv_reference(self, file_name, initial_environment):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="resolved-key"))

        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / file_name
            env_file.write_text("API_KEY=kv:https://local-vault.vault.azure.net/secrets/api-key/version-1")

            with (
                mock.patch.dict(os.environ, initial_environment, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client) as mock_client_cls,
            ):
                await _load_environment_async(
                    env_akv_ref=None,
                    env_files=[env_file],
                    env_akv_strict=True,
                    env_akv_write_env=False,
                    silent=True,
                )

                assert os.environ["API_KEY"] == "resolved-key"

            _assert_mock_akv_client_created(
                mock_client_cls,
                vault_url="https://local-vault.vault.azure.net",
                credential=credential,
            )
            client.get_secret.assert_awaited_once_with("api-key", version="version-1")

    async def test_load_environment_async_does_not_fetch_local_reference_that_loses_to_process_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / "custom.env"
            env_file.write_text("API_KEY=kv:https://local-vault.vault.azure.net/secrets/api-key")

            with (
                mock.patch.dict(os.environ, {"API_KEY": "process-key"}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential") as mock_credential_cls,
            ):
                await _load_environment_async(
                    env_akv_ref=None,
                    env_files=[env_file],
                    env_akv_strict=True,
                    env_akv_write_env=False,
                    silent=True,
                )

                assert os.environ["API_KEY"] == "process-key"

            mock_credential_cls.assert_not_called()

    async def test_load_environment_async_strict_rejects_malformed_local_akv_reference_before_authentication(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / "custom.env"
            env_file.write_text("API_KEY=kv:api-key")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential") as mock_credential_cls,
                pytest.raises(KeyVaultInitializationException, match="must use a full secret URL"),
            ):
                await _load_environment_async(
                    env_akv_ref=None,
                    env_files=[env_file],
                    env_akv_strict=True,
                    env_akv_write_env=False,
                    silent=True,
                )

            mock_credential_cls.assert_not_called()

    async def test_load_environment_async_non_strict_skips_malformed_local_akv_reference(self, caplog, capsys):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_local_file = pathlib.Path(temp_dir) / ".env.local"
            env_local_file.write_text("API_KEY=kv:api-key")

            with (
                mock.patch.dict(os.environ, {"API_KEY": "process-key"}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential") as mock_credential_cls,
                caplog.at_level("WARNING", logger="pyrit.setup.akv_initialization"),
            ):
                await _load_environment_async(
                    env_akv_ref=None,
                    env_files=[env_local_file],
                    env_akv_strict=False,
                    env_akv_write_env=False,
                    silent=False,
                )

                assert os.environ["API_KEY"] == "process-key"

            mock_credential_cls.assert_not_called()
            assert (
                "WARNING: Invalid AKV reference for environment variable 'API_KEY' will be skipped"
                in capsys.readouterr().out
            )
            assert "API_KEY" in caplog.text

    async def test_load_environment_async_non_strict_still_raises_for_missing_local_secret(self):
        credential, client = _create_mock_akv_clients()
        missing_error = ResourceNotFoundError(message="Secret was not found")
        client.get_secret = mock.AsyncMock(side_effect=missing_error)

        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / "custom.env"
            env_file.write_text("API_KEY=kv:https://local-vault.vault.azure.net/secrets/missing")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
                pytest.raises(
                    KeyVaultInitializationException, match="Failed to resolve Key Vault reference"
                ) as exc_info,
            ):
                await _load_environment_async(
                    env_akv_ref=None,
                    env_files=[env_file],
                    env_akv_strict=False,
                    env_akv_write_env=False,
                    silent=True,
                )

            assert exc_info.value.__cause__ is missing_error

    async def test_raises_error_for_nonexistent_env_file(self):
        """Test that ValueError is raised for non-existent env file."""
        nonexistent = pathlib.Path("/nonexistent/path/.env")

        with pytest.raises(ValueError, match="Environment file not found"):
            _load_environment_files(env_files=[nonexistent])

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_pyrit_with_custom_env_files(self, mock_set_memory):
        """Test initialize_pyrit_async with custom env_files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env.custom"
            env_file.write_text("CUSTOM_VAR=custom_value")

            # Should not raise an error
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[env_file], load_defaults=False)

            mock_set_memory.assert_called_once()

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_initialize_pyrit_raises_for_nonexistent_env_file(self, mock_set_memory):
        """Test that initialize_pyrit_async raises ValueError for non-existent env file."""
        nonexistent = pathlib.Path("/nonexistent/.env")

        with pytest.raises(ValueError, match="Environment file not found"):
            await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[nonexistent])

    @mock.patch("pyrit.memory.central_memory.CentralMemory.set_memory_instance")
    async def test_custom_env_files_override_default_behavior(self, mock_set_memory):
        """Test that passing custom env_files prevents loading default files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)

            # Create default files
            default_env = temp_path / ".env"
            default_env_local = temp_path / ".env.local"
            default_env.write_text("DEFAULT=value")
            default_env_local.write_text("DEFAULT_LOCAL=value")

            # Create custom file
            custom_env = temp_path / ".env.custom"
            custom_env.write_text("CUSTOM=value")

            with mock.patch.dict(os.environ, {}, clear=True):
                await initialize_pyrit_async(memory_db_type=IN_MEMORY, env_files=[custom_env], load_defaults=False)

                assert os.environ["CUSTOM"] == "value"
                assert "DEFAULT" not in os.environ
                assert "DEFAULT_LOCAL" not in os.environ


def _create_mock_akv_clients() -> tuple[mock.MagicMock, mock.MagicMock]:
    credential = mock.MagicMock()
    credential.__aenter__ = mock.AsyncMock(return_value=credential)
    credential.__aexit__ = mock.AsyncMock(return_value=None)
    client = mock.MagicMock()
    client.__aenter__ = mock.AsyncMock(return_value=client)
    client.__aexit__ = mock.AsyncMock(return_value=None)
    return credential, client


def _assert_mock_akv_client_created(
    mock_client_cls: mock.MagicMock,
    *,
    vault_url: str,
    credential: mock.MagicMock,
) -> None:
    mock_client_cls.assert_called_once()
    call_kwargs = mock_client_cls.call_args.kwargs
    assert call_kwargs["vault_url"] == vault_url
    assert call_kwargs["credential"] is credential
    retry_policy = call_kwargs["retry_policy"]
    assert retry_policy.total_retries == 3
    assert retry_policy.connect_retries == 3
    assert retry_policy.read_retries == 3
    assert retry_policy.status_retries == 3
    assert retry_policy.backoff_factor == 0.8


class TestAkvEnvironmentLoading:
    """Tests for AKV URL parsing and env loading helpers."""

    @pytest.mark.parametrize("prefix", ["kv", "akv", "azure_key_vault", "env_akv_ref"])
    def test_parse_akv_reference_accepts_aliases(self, prefix):
        secret_url = "https://myvault.vault.azure.net/secrets/api-key"

        assert _parse_akv_reference(f"{prefix}:{secret_url}") == secret_url

    @pytest.mark.parametrize(
        "value",
        [
            "env:SOURCE_VALUE",
            "literal:kv:https://myvault.vault.azure.net/secrets/api-key",
            "@Microsoft.KeyVault(SecretUri=https://myvault.vault.azure.net/secrets/api-key)",
        ],
    )
    def test_parse_akv_reference_ignores_non_akv_syntax(self, value):
        assert _parse_akv_reference(value) is None

    def test_parse_akv_secret_url_with_version(self):
        url = "https://myvault.vault.azure.net/secrets/my-secret/abc123"

        vault_url, secret_name, secret_version = _parse_akv_secret_url(url)

        assert vault_url == "https://myvault.vault.azure.net"
        assert secret_name == "my-secret"
        assert secret_version == "abc123"

    def test_parse_akv_secret_url_without_version(self):
        url = "https://myvault.vault.azure.net/secrets/my-secret"

        vault_url, secret_name, secret_version = _parse_akv_secret_url(url)

        assert vault_url == "https://myvault.vault.azure.net"
        assert secret_name == "my-secret"
        assert secret_version is None

    @pytest.mark.parametrize("dns_suffix", ["vault.azure.net", "vault.azure.cn", "vault.usgovcloudapi.net"])
    def test_parse_akv_secret_url_accepts_supported_clouds(self, dns_suffix):
        url = f"https://myvault.{dns_suffix}/secrets/my-secret/version-1"

        vault_url, secret_name, secret_version = _parse_akv_secret_url(url)

        assert vault_url == f"https://myvault.{dns_suffix}"
        assert secret_name == "my-secret"
        assert secret_version == "version-1"

    @pytest.mark.parametrize(
        "url",
        [
            "http://myvault.vault.azure.net/secrets/my-secret",
            "https://attacker.example/secrets/my-secret",
            "https://myvault.vault.azure.net.attacker.example/secrets/my-secret",
            "https://nested.myvault.vault.azure.net/secrets/my-secret",
            "https://user@myvault.vault.azure.net/secrets/my-secret",
            "https://myvault.vault.azure.net:443/secrets/my-secret",
            "https://myvault.vault.azure.net/not-secrets/my-secret",
            "https://myvault.vault.azure.net/secrets",
            "https://myvault.vault.azure.net/secrets/my-secret/",
            "https://myvault.vault.azure.net/secrets/my-secret/version/extra",
            "https://myvault.vault.azure.net/secrets/my-secret?api-version=7.4",
            "https://myvault.vault.azure.net/secrets/my-secret#fragment",
            "https://myvault.vault.azure.net/secrets/my%2Fsecret",
        ],
    )
    def test_parse_akv_secret_url_invalid_raises(self, url):
        with pytest.raises(ValueError, match="Invalid AKV secret URL"):
            _parse_akv_secret_url(url)

    async def test_load_env_from_akv_async_rejects_non_azure_host_before_authentication(self):
        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential") as mock_credential_cls,
            mock.patch("pyrit.setup.akv_initialization._create_akv_secret_client") as mock_create_client,
            pytest.raises(KeyVaultInitializationException, match="attacker.example"),
        ):
            await _load_env_from_akv_async(
                secret_url="https://attacker.example/secrets/bootstrap",
                silent=True,
            )

        mock_credential_cls.assert_not_called()
        mock_create_client.assert_not_called()

    async def test_load_env_from_akv_async_loads_bootstrap_and_resolves_child_secrets(self):
        credential, client = _create_mock_akv_clients()
        root_document = (
            "DIRECT=from-bootstrap\n"
            "FROM_ENV=${SOURCE_VALUE}\n"
            "FROM_KV=kv:https://myvault.vault.azure.net/secrets/api-key\n"
            "PINNED_KV=kv:https://myvault.vault.azure.net/secrets/api-key/version-2\n"
            "TERMINAL=kv:https://myvault.vault.azure.net/secrets/terminal\n"
            "A=one\nB=${A}\nA=two\nC=${A}"
        )
        client.get_secret = mock.AsyncMock(
            side_effect=[
                types.SimpleNamespace(value=root_document),
                types.SimpleNamespace(value="api-key-value"),
                types.SimpleNamespace(value="pinned-key-value"),
                types.SimpleNamespace(value="kv:https://myvault.vault.azure.net/secrets/not-followed"),
            ]
        )
        secret_url = "https://myvault.vault.azure.net/secrets/bootstrap/v1"

        with (
            mock.patch.dict(os.environ, {"SOURCE_VALUE": "ambient-value"}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential) as mock_credential_cls,
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client) as mock_client_cls,
            mock.patch("pyrit.setup.akv_initialization._print_msg") as mock_print_msg,
        ):
            await _load_env_from_akv_async(secret_url=secret_url, silent=True)

            assert os.environ["DIRECT"] == "from-bootstrap"
            assert os.environ["FROM_ENV"] == "ambient-value"
            assert os.environ["FROM_KV"] == "api-key-value"
            assert os.environ["PINNED_KV"] == "pinned-key-value"
            assert os.environ["TERMINAL"] == "kv:https://myvault.vault.azure.net/secrets/not-followed"
            assert os.environ["A"] == "two"
            assert os.environ["B"] == "one"
            assert os.environ["C"] == "two"

        mock_credential_cls.assert_called_once_with()
        _assert_mock_akv_client_created(
            mock_client_cls,
            vault_url="https://myvault.vault.azure.net",
            credential=credential,
        )
        assert client.get_secret.await_args_list == [
            mock.call("bootstrap", version="v1"),
            mock.call("api-key", version=None),
            mock.call("api-key", version="version-2"),
            mock.call("terminal", version=None),
        ]
        credential.__aenter__.assert_awaited_once()
        credential.__aexit__.assert_awaited_once()
        client.__aenter__.assert_awaited_once()
        client.__aexit__.assert_awaited_once()
        mock_print_msg.assert_called_once()

    async def test_load_env_from_akv_async_preserves_process_values_without_fetching_overridden_child(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(
            return_value=types.SimpleNamespace(
                value=("DIRECT=from-bootstrap\nFROM_KV=kv:https://myvault.vault.azure.net/secrets/api-key")
            )
        )
        secret_url = "https://myvault.vault.azure.net/secrets/bootstrap"

        with (
            mock.patch.dict(
                os.environ,
                {"DIRECT": "from-process", "FROM_KV": "process-key"},
                clear=True,
            ),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
        ):
            await _load_env_from_akv_async(secret_url=secret_url, silent=True)

            assert os.environ["DIRECT"] == "from-process"
            assert os.environ["FROM_KV"] == "process-key"

        client.get_secret.assert_awaited_once_with("bootstrap", version=None)

    async def test_load_env_from_akv_async_rejects_short_secret_name(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="API_KEY=kv:api-key"))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(ValueError, match="must use a full secret URL"),
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                silent=True,
            )

    @pytest.mark.parametrize(
        "reference_url",
        [
            "https://other-vault.vault.azure.net/secrets/api-key",
            "https://other-vault.vault.azure.net/secrets/api-key/version-1",
        ],
    )
    async def test_load_env_from_akv_async_rejects_cross_vault_reference(self, reference_url):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=f"API_KEY=kv:{reference_url}"))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(ValueError, match="Cross-vault AKV reference"),
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                silent=True,
            )

    async def test_load_env_from_akv_async_empty_secret_raises(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=None))

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(ValueError, match="has no value"),
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/my-secret",
                silent=True,
            )

        credential.__aexit__.assert_awaited_once()
        client.__aexit__.assert_awaited_once()

    async def test_load_env_from_akv_async_without_entries_raises(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="# comments only\n"))

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(ValueError, match="contains no environment entries"),
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/my-secret",
                silent=True,
            )

        credential.__aexit__.assert_awaited_once()
        client.__aexit__.assert_awaited_once()

    @pytest.mark.parametrize(
        ("document", "error"),
        [
            ("GOOD=resolved\n=malformed\nOTHER=resolved", "malformed entries at lines: 2"),
            ("MISSING_VALUE\n", "variables without values: MISSING_VALUE"),
        ],
    )
    async def test_load_env_from_akv_async_rejects_non_assignments(self, document, error):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=document))

        with mock.patch.dict(os.environ, {}, clear=True):
            with (
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
                pytest.raises(ValueError, match=error),
            ):
                await _load_env_from_akv_async(
                    secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                    silent=True,
                )

            assert "GOOD" not in os.environ
            assert "OTHER" not in os.environ

    async def test_load_env_from_akv_async_wraps_malformed_bootstrap(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="=malformed"))

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(KeyVaultInitializationException, match="malformed entries") as exc_info,
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                silent=True,
            )

        assert isinstance(exc_info.value.__cause__, ValueError)

    async def test_load_env_from_akv_async_wraps_missing_child_secret(self):
        credential, client = _create_mock_akv_clients()
        missing_error = ResourceNotFoundError(message="Secret was not found")
        client.get_secret = mock.AsyncMock(
            side_effect=[
                types.SimpleNamespace(value="API_KEY=kv:https://myvault.vault.azure.net/secrets/missing"),
                missing_error,
            ]
        )

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(KeyVaultInitializationException, match="Failed to resolve Key Vault reference") as exc_info,
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                silent=True,
            )

        assert exc_info.value.__cause__ is missing_error

    async def test_load_env_from_akv_async_allows_empty_assignment(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="EMPTY="))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                silent=True,
            )

            assert os.environ["EMPTY"] == ""

    async def test_load_env_from_akv_async_allows_empty_child_secret(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(
            side_effect=[
                types.SimpleNamespace(value="EMPTY=kv:https://myvault.vault.azure.net/secrets/empty-secret"),
                types.SimpleNamespace(value=""),
            ]
        )

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                silent=True,
            )

            assert os.environ["EMPTY"] == ""
        assert client.get_secret.await_args_list[-1] == mock.call("empty-secret", version=None)

    async def test_load_env_from_akv_async_non_strict_warns_and_skips_invalid_entries(self, caplog, capsys):
        credential, client = _create_mock_akv_clients()
        document = "GOOD=resolved\n=malformed\nMISSING_VALUE\nOTHER=also-resolved"
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=document))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            caplog.at_level("WARNING", logger="pyrit.setup.akv_initialization"),
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                strict=False,
                silent=False,
            )

            assert os.environ["GOOD"] == "resolved"
            assert os.environ["OTHER"] == "also-resolved"

        output = capsys.readouterr().out
        assert "WARNING: AKV environment document contains invalid entries that will be skipped" in output
        assert "malformed entries at lines: 2" in output
        assert "variables without values: MISSING_VALUE" in output
        assert "GOOD" not in caplog.text
        assert "resolved" not in caplog.text

    async def test_load_env_from_akv_async_non_strict_warns_and_skips_invalid_reference(self, caplog, capsys):
        credential, client = _create_mock_akv_clients()
        document = "GOOD=resolved\nBAD=kv:short-name\nOTHER=also-resolved"
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=document))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            caplog.at_level("WARNING", logger="pyrit.setup.akv_initialization"),
        ):
            resolved_document = await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                strict=False,
                silent=False,
                resolve_references_for_output=True,
            )

            assert os.environ["GOOD"] == "resolved"
            assert os.environ["OTHER"] == "also-resolved"
            assert "BAD" not in os.environ

        assert "BAD=" not in resolved_document
        assert (
            "WARNING: Invalid AKV reference for environment variable 'BAD' will be skipped" in capsys.readouterr().out
        )
        assert "BAD" in caplog.text
        client.get_secret.assert_awaited_once_with("bootstrap", version=None)

    async def test_load_env_from_akv_async_non_strict_silent_logs_warning(self, caplog, capsys):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="GOOD=resolved\nMISSING_VALUE"))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            caplog.at_level("WARNING", logger="pyrit.setup.akv_initialization"),
        ):
            await _load_env_from_akv_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                strict=False,
                silent=True,
            )

        assert capsys.readouterr().out == ""
        assert "variables without values: MISSING_VALUE" in caplog.text

    async def test_load_env_from_akv_async_child_failure_keeps_loaded_bootstrap_values(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(
            side_effect=[
                types.SimpleNamespace(
                    value=("GOOD=resolved\nBAD=kv:https://myvault.vault.azure.net/secrets/missing-value")
                ),
                types.SimpleNamespace(value=None),
            ]
        )

        with mock.patch.dict(os.environ, {}, clear=True):
            with (
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
                pytest.raises(ValueError, match="has no value"),
            ):
                await _load_env_from_akv_async(
                    secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                    silent=True,
                )

            assert os.environ["GOOD"] == "resolved"
            assert os.environ["BAD"] == "kv:https://myvault.vault.azure.net/secrets/missing-value"
