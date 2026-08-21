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

from pyrit.exceptions import KeyVaultInitializationException
from pyrit.setup import IN_MEMORY, initialize_pyrit_async
from pyrit.setup.environment_loading import (
    _AkvEnvironmentDocument,
    _fetch_akv_document_async,
    _parse_akv_reference,
    _parse_akv_secret_url,
    _warn_about_dotenv_file,
    load_environment_async,
    load_environment_files,
)


class TestLoadEnvironmentFiles:
    """Tests for load_environment_files and the env_files initialization parameter."""

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_loads_default_env_files_when_none_provided(self, mock_config_path):
        """Test that default .env and .env.local files are loaded when env_files is None."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_local_file = temp_path / ".env.local"
            env_file.write_text("VAR1=value1")
            env_local_file.write_text("VAR2=value2")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = load_environment_files(env_files=None)

                assert loaded is True
                assert os.environ["VAR1"] == "value1"
                assert os.environ["VAR2"] == "value2"

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_only_loads_existing_default_files(self, mock_config_path):
        """Test that only existing default files are loaded."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_file.write_text("VAR1=value1")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = load_environment_files(env_files=None)

                assert loaded is True
                assert os.environ["VAR1"] == "value1"

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_default_env_preserves_process_environment(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env").write_text("VAR=legacy\nLEGACY_ONLY=legacy")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {"VAR": "process"}, clear=True):
                loaded = load_environment_files(env_files=None, silent=True)

                assert loaded is True
                assert os.environ["VAR"] == "process"
                assert os.environ["LEGACY_ONLY"] == "legacy"

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_default_env_local_overrides_process_environment_and_env(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env").write_text("VAR=legacy")
            (temp_path / ".env.local").write_text("VAR=local")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {"VAR": "process"}, clear=True):
                loaded = load_environment_files(env_files=None, silent=True)

                assert loaded is True
                assert os.environ["VAR"] == "local"

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_excludes_default_env_when_loading_local_override(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_local_file = temp_path / ".env.local"
            env_file.write_text("VAR=base")
            env_local_file.write_text("VAR=local")

            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = load_environment_files(env_files=None, include_default_base=False)

                assert loaded is True
                assert os.environ["VAR"] == "local"

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_returns_false_when_no_default_files_exist(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = load_environment_files(env_files=None)

                assert loaded is False
                assert os.environ == {}

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
    def test_auto_discovered_env_warns_about_plaintext(self, mock_config_path, caplog, capsys):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_file = temp_path / ".env"
            env_file.write_text("VAR=base")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with (
                caplog.at_level("WARNING", logger="pyrit.setup.environment_loading"),
                warnings.catch_warnings(),
            ):
                warnings.simplefilter("error", DeprecationWarning)
                load_environment_files(env_files=None)

        output = capsys.readouterr().out
        assert f"Auto-discovered plaintext environment file {env_file} will be loaded" in output
        assert "Azure Key Vault through env_akv_ref is more secure" in output
        assert "build_scripts.export_akv_environment" in output
        assert "~/.pyrit/.env_akv" in caplog.text

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
    def test_default_discovery_does_not_load_env_akv(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env_akv").write_text("EXPORTED_SECRET=not-loaded", encoding="utf-8")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = load_environment_files(env_files=None, silent=True)

                assert loaded is False
                assert "EXPORTED_SECRET" not in os.environ

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
    def test_explicit_env_file_does_not_emit_auto_discovery_warning(self, mock_config_path, caplog):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            explicit_env = temp_path / ".env"
            explicit_env.write_text("VAR=explicit")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with caplog.at_level("WARNING", logger="pyrit.setup.environment_loading"):
                loaded = load_environment_files(env_files=[explicit_env], silent=True)

        assert loaded is True
        assert "Azure Key Vault through env_akv_ref is more secure" not in caplog.text

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
    def test_akv_dotenv_warning_respects_silent(self, mock_config_path, caplog, capsys):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env").write_text("VAR=base")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with caplog.at_level("WARNING", logger="pyrit.setup.environment_loading"):
                _warn_about_dotenv_file(
                    env_file=temp_path / ".env",
                    ignored_for_akv=True,
                    silent=True,
                )

        assert capsys.readouterr().out == ""
        assert "will be ignored because env_akv_ref is configured" in caplog.text
        assert "build_scripts.export_akv_environment" in caplog.text

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
    async def test_akv_ignores_auto_discovered_env_and_loads_env_local(self, mock_config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            (temp_path / ".env").write_text("VALUE=legacy")
            (temp_path / ".env.local").write_text("VALUE=local")
            mock_config_path.__truediv__ = lambda self, other: temp_path / other

            with (
                mock.patch(
                    "pyrit.setup.environment_loading._fetch_akv_document_async",
                    new_callable=mock.AsyncMock,
                    return_value=_AkvEnvironmentDocument(
                        content="VALUE=akv\n",
                        vault_url="https://vault.vault.azure.net",
                    ),
                ),
                mock.patch("pyrit.setup.environment_loading._load_environment_files") as mock_load_files,
            ):
                await load_environment_async(
                    env_akv_ref=["https://vault.vault.azure.net/secrets/bootstrap"],
                    env_files=None,
                    env_akv_strict=True,
                    silent=True,
                )

            assert mock_load_files.call_args.kwargs["env_files"] is None
            assert mock_load_files.call_args.kwargs["include_default_base"] is False

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
                loaded = load_environment_files(env_files=[env1, env2, env3])

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
                loaded = load_environment_files(env_files=[first_file, second_file, local_file], silent=True)

                assert loaded is True
                assert os.environ["PROCESS_VALUE"] == "local"
                assert os.environ["FILE_VALUE"] == "local"
                assert os.environ["SECOND_ONLY"] == "second"

            with mock.patch.dict(os.environ, {"PROCESS_VALUE": "process"}, clear=True):
                load_environment_files(env_files=[first_file, second_file], silent=True)

                assert os.environ["PROCESS_VALUE"] == "process"
                assert os.environ["FILE_VALUE"] == "first"

    async def test_load_environment_files_interpolates_in_assignment_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / ".env"
            env_file.write_text("A=one\nB=${A}\nA=two\nC=${A}")

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = load_environment_files(env_files=[env_file], silent=True)

                assert loaded is True
                assert os.environ["A"] == "two"
                assert os.environ["B"] == "one"
                assert os.environ["C"] == "two"

    async def test_load_environment_files_honors_python_dotenv_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / ".env"
            env_file.write_text("DISABLED_VALUE=not-loaded")

            with mock.patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "true"}, clear=True):
                loaded = load_environment_files(env_files=[env_file], silent=True)

                assert loaded is True
                assert "DISABLED_VALUE" not in os.environ

    async def test_load_environment_async_skips_sources_when_python_dotenv_disabled(self):
        with (
            mock.patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "true"}, clear=True),
            mock.patch(
                "pyrit.setup.environment_loading._fetch_akv_document_async", new_callable=mock.AsyncMock
            ) as mock_fetch_akv,
            mock.patch("pyrit.setup.environment_loading._load_environment_files") as mock_load_files,
        ):
            await load_environment_async(
                env_akv_ref=None,
                env_files=None,
                env_akv_strict=True,
                silent=True,
            )

        mock_fetch_akv.assert_not_awaited()
        mock_load_files.assert_not_called()

    @pytest.mark.parametrize(
        ("env_akv_ref", "env_files"),
        [
            (["https://vault.vault.azure.net/secrets/bootstrap"], None),
            (None, [pathlib.Path("configured.env")]),
        ],
    )
    async def test_load_environment_async_skips_configured_sources_when_python_dotenv_disabled(
        self, env_akv_ref, env_files
    ):
        with (
            mock.patch.dict(
                os.environ,
                {"PYTHON_DOTENV_DISABLED": "true", "AMBIENT_VALUE": "preserved"},
                clear=True,
            ),
            mock.patch(
                "pyrit.setup.environment_loading._fetch_akv_document_async", new_callable=mock.AsyncMock
            ) as mock_fetch_akv,
            mock.patch("pyrit.setup.environment_loading._load_environment_files") as mock_load_files,
        ):
            await load_environment_async(
                env_akv_ref=env_akv_ref,
                env_files=env_files,
                env_akv_strict=True,
                silent=True,
            )

            assert os.environ["AMBIENT_VALUE"] == "preserved"

        mock_fetch_akv.assert_not_awaited()
        mock_load_files.assert_not_called()

    async def test_runtime_does_not_fetch_akv_reference_overridden_by_env_local(self):
        credential, client = _create_mock_akv_clients()
        bootstrap_document = "API_KEY=kv:https://vault.vault.azure.net/secrets/api-key\nAKV_ONLY=akv\n"
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=bootstrap_document))

        with tempfile.TemporaryDirectory() as temp_dir:
            local_file = pathlib.Path(temp_dir) / ".env.local"
            local_file.write_text("API_KEY=local-key\n", encoding="utf-8")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            ):
                await load_environment_async(
                    env_akv_ref=["https://vault.vault.azure.net/secrets/bootstrap"],
                    env_files=[local_file],
                    env_akv_strict=True,
                    silent=True,
                )

                assert os.environ["API_KEY"] == "local-key"
                assert os.environ["AKV_ONLY"] == "akv"

        client.get_secret.assert_awaited_once_with("bootstrap", version=None)

    async def test_runtime_resolves_local_alias_after_all_sources_are_loaded(self):
        credential, client = _create_mock_akv_clients()
        bootstrap_document = "A=kv:https://vault.vault.azure.net/secrets/key\n"
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=bootstrap_document))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            ordinary_file = temp_path / "ordinary.env"
            ordinary_file.write_text("B=${A}\n", encoding="utf-8")
            local_file = temp_path / ".env.local"
            local_file.write_text("A=literal\n", encoding="utf-8")

            async def resolve_by_variable_name(**kwargs):
                return f"resolved-for-{kwargs['variable_name']}"

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
                mock.patch(
                    "pyrit.setup.environment_loading._fetch_akv_secret_value_async",
                    new_callable=mock.AsyncMock,
                    side_effect=resolve_by_variable_name,
                ) as mock_fetch_child,
            ):
                await load_environment_async(
                    env_akv_ref=["https://vault.vault.azure.net/secrets/bootstrap"],
                    env_files=[ordinary_file, local_file],
                    env_akv_strict=True,
                    silent=True,
                )

                assert os.environ["A"] == "literal"
                assert os.environ["B"] == "resolved-for-B"

        mock_fetch_child.assert_awaited_once()
        assert mock_fetch_child.await_args is not None
        assert mock_fetch_child.await_args.kwargs["variable_name"] == "B"

    @pytest.mark.parametrize("fallback_source", ["akv", "file"])
    async def test_non_strict_runtime_uses_blocked_lower_candidate_after_malformed_akv_winner(self, fallback_source):
        credential, client = _create_mock_akv_clients()
        documents = {
            "https://vault.vault.azure.net/secrets/first": "API_KEY=kv:short\n",
            "https://vault.vault.azure.net/secrets/second": (
                "API_KEY=kv:https://vault.vault.azure.net/secrets/fallback-key\n"
            ),
        }

        async def fetch_document(secret_url, **kwargs):
            return _AkvEnvironmentDocument(
                content=documents[secret_url],
                vault_url="https://vault.vault.azure.net",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            env_files: list[pathlib.Path] = []
            env_akv_ref = ["https://vault.vault.azure.net/secrets/first"]
            if fallback_source == "akv":
                env_akv_ref.append("https://vault.vault.azure.net/secrets/second")
            else:
                ordinary_file = pathlib.Path(temp_dir) / "ordinary.env"
                ordinary_file.write_text(
                    "API_KEY=kv:https://local-vault.vault.azure.net/secrets/fallback-key\n",
                    encoding="utf-8",
                )
                env_files.append(ordinary_file)

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch(
                    "pyrit.setup.environment_loading._fetch_akv_document_async",
                    new_callable=mock.AsyncMock,
                    side_effect=fetch_document,
                ),
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            ):
                client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="resolved-fallback"))
                await load_environment_async(
                    env_akv_ref=env_akv_ref,
                    env_files=env_files,
                    env_akv_strict=False,
                    silent=True,
                )

                assert os.environ["API_KEY"] == "resolved-fallback"

            client.get_secret.assert_awaited_once_with("fallback-key", version=None)

    async def test_direct_local_file_loader_keeps_pyrit_references_literal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = pathlib.Path(temp_dir) / ".env"
            env_file.write_text(
                "BASE_VALUE=base\nKV_REFERENCE=kv:api-key\nENV_REFERENCE=env:SOURCE_VALUE\nINTERPOLATED=${BASE_VALUE}"
            )

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = load_environment_files(env_files=[env_file], silent=True)

                assert loaded is True
                assert os.environ["KV_REFERENCE"] == "kv:api-key"
                assert os.environ["ENV_REFERENCE"] == "env:SOURCE_VALUE"
                assert os.environ["INTERPOLATED"] == "base"

    @mock.patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH")
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

            with mock.patch.dict(os.environ, {}, clear=True):
                loaded = load_environment_files(env_files=None, silent=True)

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
                await load_environment_async(
                    env_akv_ref=None,
                    env_files=[env_file],
                    env_akv_strict=True,
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
                await load_environment_async(
                    env_akv_ref=None,
                    env_files=[env_file],
                    env_akv_strict=True,
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
                await load_environment_async(
                    env_akv_ref=None,
                    env_files=[env_file],
                    env_akv_strict=True,
                    silent=True,
                )

            mock_credential_cls.assert_not_called()

    async def test_load_environment_async_non_strict_skips_malformed_local_akv_reference(self, caplog, capsys):
        with tempfile.TemporaryDirectory() as temp_dir:
            env_local_file = pathlib.Path(temp_dir) / ".env.local"
            env_local_file.write_text("API_KEY=kv:api-key")
            process_value = "kv:https://process-vault.vault.azure.net/secrets/do-not-resolve"

            with (
                mock.patch.dict(os.environ, {"API_KEY": process_value}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential") as mock_credential_cls,
                caplog.at_level("WARNING", logger="pyrit.setup.environment_loading"),
            ):
                await load_environment_async(
                    env_akv_ref=None,
                    env_files=[env_local_file],
                    env_akv_strict=False,
                    silent=False,
                )

                assert os.environ["API_KEY"] == process_value

            mock_credential_cls.assert_not_called()
            assert (
                "WARNING: Invalid AKV reference for environment variable 'API_KEY' will be skipped"
                in capsys.readouterr().out
            )
            assert "API_KEY" in caplog.text

    @pytest.mark.parametrize("malformed_override_count", [1, 2])
    async def test_non_strict_falls_back_to_valid_reference_after_malformed_overrides(
        self, malformed_override_count, caplog
    ):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="resolved-key"))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            ordinary_file = temp_path / "ordinary.env"
            ordinary_file.write_text("API_KEY=kv:https://local-vault.vault.azure.net/secrets/api-key")
            env_files = [ordinary_file]
            for index in range(malformed_override_count):
                local_file = temp_path / str(index) / ".env.local"
                local_file.parent.mkdir()
                local_file.write_text(f"API_KEY=kv:short-{index}")
                env_files.append(local_file)

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
                caplog.at_level("WARNING", logger="pyrit.setup.environment_loading"),
            ):
                await load_environment_async(
                    env_akv_ref=None,
                    env_files=env_files,
                    env_akv_strict=False,
                    silent=True,
                )

                assert os.environ["API_KEY"] == "resolved-key"

            client.get_secret.assert_awaited_once_with("api-key", version=None)
            warnings = [record for record in caplog.records if "Invalid AKV reference" in record.message]
            assert len(warnings) == malformed_override_count

    @pytest.mark.parametrize(
        ("fallback_value", "expected_value"),
        [("plain-value", "plain-value"), (None, None)],
    )
    async def test_non_strict_malformed_override_uses_literal_or_no_fallback(self, fallback_value, expected_value):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            env_files: list[pathlib.Path] = []
            if fallback_value is not None:
                ordinary_file = temp_path / "ordinary.env"
                ordinary_file.write_text(f"API_KEY={fallback_value}")
                env_files.append(ordinary_file)
            local_file = temp_path / ".env.local"
            local_file.write_text("API_KEY=kv:short")
            env_files.append(local_file)

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential") as mock_credential_cls,
            ):
                await load_environment_async(
                    env_akv_ref=None,
                    env_files=env_files,
                    env_akv_strict=False,
                    silent=True,
                )

                assert os.environ.get("API_KEY") == expected_value

            mock_credential_cls.assert_not_called()

    async def test_strict_malformed_override_does_not_resolve_valid_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            ordinary_file = temp_path / "ordinary.env"
            ordinary_file.write_text("API_KEY=kv:https://local-vault.vault.azure.net/secrets/api-key")
            local_file = temp_path / ".env.local"
            local_file.write_text("API_KEY=kv:short")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential") as mock_credential_cls,
                pytest.raises(KeyVaultInitializationException, match="must use a full secret URL"),
            ):
                await load_environment_async(
                    env_akv_ref=None,
                    env_files=[ordinary_file, local_file],
                    env_akv_strict=True,
                    silent=True,
                )

            mock_credential_cls.assert_not_called()

    async def test_highest_valid_local_reference_wins(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="local-key"))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            ordinary_file = temp_path / "ordinary.env"
            ordinary_file.write_text("API_KEY=kv:https://local-vault.vault.azure.net/secrets/ordinary-key")
            local_file = temp_path / ".env.local"
            local_file.write_text("API_KEY=kv:https://local-vault.vault.azure.net/secrets/local-key")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            ):
                await load_environment_async(
                    env_akv_ref=None,
                    env_files=[ordinary_file, local_file],
                    env_akv_strict=True,
                    silent=True,
                )

                assert os.environ["API_KEY"] == "local-key"

            client.get_secret.assert_awaited_once_with("local-key", version=None)

    async def test_non_strict_resolves_interpolated_fallback_candidate(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="resolved-key"))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            ordinary_file = temp_path / "ordinary.env"
            ordinary_file.write_text(
                "SECRET_URL=https://local-vault.vault.azure.net/secrets/api-key\nAPI_KEY=kv:${SECRET_URL}"
            )
            local_file = temp_path / ".env.local"
            local_file.write_text("API_KEY=kv:short")

            with (
                mock.patch.dict(os.environ, {}, clear=True),
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            ):
                await load_environment_async(
                    env_akv_ref=None,
                    env_files=[ordinary_file, local_file],
                    env_akv_strict=False,
                    silent=True,
                )

                assert os.environ["API_KEY"] == "resolved-key"

            client.get_secret.assert_awaited_once_with("api-key", version=None)

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
                await load_environment_async(
                    env_akv_ref=None,
                    env_files=[env_file],
                    env_akv_strict=False,
                    silent=True,
                )

            assert exc_info.value.__cause__ is missing_error

    async def test_raises_error_for_nonexistent_env_file(self):
        """Test that ValueError is raised for non-existent env file."""
        nonexistent = pathlib.Path("/nonexistent/path/.env")

        with pytest.raises(ValueError, match="Environment file not found"):
            load_environment_files(env_files=[nonexistent])

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

    async def test_fetch_akv_document_async_rejects_non_azure_host_before_authentication(self):
        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential") as mock_credential_cls,
            mock.patch("pyrit.setup.environment_loading._create_akv_secret_client") as mock_create_client,
            pytest.raises(KeyVaultInitializationException, match="attacker.example"),
        ):
            await _fetch_akv_document_async(
                secret_url="https://attacker.example/secrets/bootstrap",
                silent=True,
            )

        mock_credential_cls.assert_not_called()
        mock_create_client.assert_not_called()

    async def test_fetch_akv_document_async_returns_validated_document(self):
        credential, client = _create_mock_akv_clients()
        root_document = (
            "DIRECT=from-bootstrap\n"
            "FROM_ENV=${SOURCE_VALUE}\n"
            "FROM_KV=kv:https://myvault.vault.azure.net/secrets/api-key\n"
            "PINNED_KV=kv:https://myvault.vault.azure.net/secrets/api-key/version-2\n"
            "TERMINAL=kv:https://myvault.vault.azure.net/secrets/terminal\n"
            "A=one\nB=${A}\nA=two\nC=${A}"
        )
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=root_document))
        secret_url = "https://myvault.vault.azure.net/secrets/bootstrap/v1"

        with (
            mock.patch.dict(os.environ, {"SOURCE_VALUE": "ambient-value"}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential) as mock_credential_cls,
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client) as mock_client_cls,
            mock.patch("pyrit.setup.environment_loading._print_msg") as mock_print_msg,
        ):
            document = await _fetch_akv_document_async(secret_url=secret_url, silent=True)

            assert document.content == root_document
            assert document.vault_url == "https://myvault.vault.azure.net"
            assert os.environ == {"SOURCE_VALUE": "ambient-value"}

        mock_credential_cls.assert_called_once_with()
        _assert_mock_akv_client_created(
            mock_client_cls,
            vault_url="https://myvault.vault.azure.net",
            credential=credential,
        )
        client.get_secret.assert_awaited_once_with("bootstrap", version="v1")
        credential.__aenter__.assert_awaited_once()
        credential.__aexit__.assert_awaited_once()
        client.__aenter__.assert_awaited_once()
        client.__aexit__.assert_awaited_once()
        mock_print_msg.assert_called_once()

    async def test_runtime_preserves_process_values_without_fetching_overridden_akv_child(self):
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
            await load_environment_async(
                env_akv_ref=[secret_url],
                env_files=[],
                env_akv_strict=True,
                silent=True,
            )

            assert os.environ["DIRECT"] == "from-process"
            assert os.environ["FROM_KV"] == "process-key"

        client.get_secret.assert_awaited_once_with("bootstrap", version=None)

    async def test_runtime_rejects_short_akv_secret_name(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="API_KEY=kv:api-key"))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(ValueError, match="must use a full secret URL"),
        ):
            await load_environment_async(
                env_akv_ref=["https://myvault.vault.azure.net/secrets/bootstrap"],
                env_files=[],
                env_akv_strict=True,
                silent=True,
            )

    @pytest.mark.parametrize(
        "reference_url",
        [
            "https://other-vault.vault.azure.net/secrets/api-key",
            "https://other-vault.vault.azure.net/secrets/api-key/version-1",
        ],
    )
    async def test_runtime_rejects_cross_vault_reference(self, reference_url):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=f"API_KEY=kv:{reference_url}"))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(ValueError, match="Cross-vault AKV reference"),
        ):
            await load_environment_async(
                env_akv_ref=["https://myvault.vault.azure.net/secrets/bootstrap"],
                env_files=[],
                env_akv_strict=True,
                silent=True,
            )

    async def test_fetch_akv_document_async_empty_secret_raises(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=None))

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(ValueError, match="has no value"),
        ):
            await _fetch_akv_document_async(
                secret_url="https://myvault.vault.azure.net/secrets/my-secret",
                silent=True,
            )

        credential.__aexit__.assert_awaited_once()
        client.__aexit__.assert_awaited_once()

    async def test_fetch_akv_document_async_without_entries_raises(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="# comments only\n"))

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(ValueError, match="contains no environment entries"),
        ):
            await _fetch_akv_document_async(
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
    async def test_fetch_akv_document_async_rejects_non_assignments(self, document, error):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=document))

        with mock.patch.dict(os.environ, {}, clear=True):
            with (
                mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
                mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
                pytest.raises(ValueError, match=error),
            ):
                await _fetch_akv_document_async(
                    secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                    silent=True,
                )

            assert "GOOD" not in os.environ
            assert "OTHER" not in os.environ

    async def test_fetch_akv_document_async_wraps_malformed_bootstrap(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="=malformed"))

        with (
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            pytest.raises(KeyVaultInitializationException, match="malformed entries") as exc_info,
        ):
            await _fetch_akv_document_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                silent=True,
            )

        assert isinstance(exc_info.value.__cause__, ValueError)

    async def test_runtime_wraps_missing_child_secret(self):
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
            await load_environment_async(
                env_akv_ref=["https://myvault.vault.azure.net/secrets/bootstrap"],
                env_files=[],
                env_akv_strict=True,
                silent=True,
            )

        assert exc_info.value.__cause__ is missing_error

    async def test_runtime_allows_empty_assignment(self):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="EMPTY="))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
        ):
            await load_environment_async(
                env_akv_ref=["https://myvault.vault.azure.net/secrets/bootstrap"],
                env_files=[],
                env_akv_strict=True,
                silent=True,
            )

            assert os.environ["EMPTY"] == ""

    async def test_runtime_allows_empty_child_secret(self):
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
            await load_environment_async(
                env_akv_ref=["https://myvault.vault.azure.net/secrets/bootstrap"],
                env_files=[],
                env_akv_strict=True,
                silent=True,
            )

            assert os.environ["EMPTY"] == ""
        assert client.get_secret.await_args_list[-1] == mock.call("empty-secret", version=None)

    async def test_fetch_akv_document_async_non_strict_warns_and_skips_invalid_entries(self, caplog, capsys):
        credential, client = _create_mock_akv_clients()
        document = "GOOD=resolved\n=malformed\nMISSING_VALUE\nOTHER=also-resolved"
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value=document))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            caplog.at_level("WARNING", logger="pyrit.setup.environment_loading"),
        ):
            fetched_document = await _fetch_akv_document_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                strict=False,
                silent=False,
            )

            assert fetched_document.content == "GOOD=resolved\nOTHER=also-resolved"
            assert os.environ == {}

        output = capsys.readouterr().out
        assert "WARNING: AKV environment document contains invalid entries that will be skipped" in output
        assert "malformed entries at lines: 2" in output
        assert "variables without values: MISSING_VALUE" in output
        assert "GOOD" not in caplog.text
        assert "resolved" not in caplog.text

    async def test_fetch_akv_document_async_non_strict_silent_logs_warning(self, caplog, capsys):
        credential, client = _create_mock_akv_clients()
        client.get_secret = mock.AsyncMock(return_value=types.SimpleNamespace(value="GOOD=resolved\nMISSING_VALUE"))

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
            mock.patch("azure.keyvault.secrets.aio.SecretClient", return_value=client),
            caplog.at_level("WARNING", logger="pyrit.setup.environment_loading"),
        ):
            await _fetch_akv_document_async(
                secret_url="https://myvault.vault.azure.net/secrets/bootstrap",
                strict=False,
                silent=True,
            )

        assert capsys.readouterr().out == ""
        assert "variables without values: MISSING_VALUE" in caplog.text

    async def test_runtime_child_failure_keeps_loaded_bootstrap_values(self):
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
                await load_environment_async(
                    env_akv_ref=["https://myvault.vault.azure.net/secrets/bootstrap"],
                    env_files=[],
                    env_akv_strict=True,
                    silent=True,
                )

            assert os.environ["GOOD"] == "resolved"
            assert os.environ["BAD"] == "kv:https://myvault.vault.azure.net/secrets/missing-value"
