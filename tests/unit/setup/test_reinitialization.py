# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Repeatable setup uses fresh scripts, replacement values, and the same memory."""

import os
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.memory import AzureSQLMemory, CentralMemory, SQLiteMemory
from pyrit.registry import InitializerRegistry, TargetRegistry
from pyrit.setup.configuration_loader import ConfigurationLoader
from pyrit.setup.environment_loading import resolve_environment_async
from pyrit.setup.initialization import reset_setup_registries, validate_reinitialization_memory


async def test_preflight_uses_isolated_registry_without_changing_live_state() -> None:
    live_registry = InitializerRegistry.get_registry_singleton()
    config = ConfigurationLoader(
        memory_db_type="in_memory",
        env_files=[],
        initialization_scripts=[],
        enable_live_reinitialization=True,
    )

    with patch.dict(os.environ, {"EXISTING": "value"}, clear=True):
        prepared = await config.preflight_reinitialization_async(environment_values={"REPLACEMENT": "new"})
        assert dict(os.environ) == {"EXISTING": "value"}

    assert prepared.initializer_registry is not live_registry
    assert InitializerRegistry.get_registry_singleton() is live_registry
    assert [type(initializer).__name__ for initializer in prepared.initializers] == [
        "TechniqueInitializer",
        "TargetInitializer",
    ]


async def test_replacement_precedence_interpolation_empty_and_omission(tmp_path: Path) -> None:
    base, other, local = tmp_path / ".env", tmp_path / "other.env", tmp_path / ".env.local"
    base.write_text("VALUE=new\nEMPTY=\nINTERPOLATED=${VALUE}/suffix\n", encoding="utf-8")
    other.write_text("VALUE=ignored\n", encoding="utf-8")
    local.write_text("VALUE=local\n", encoding="utf-8")
    with patch.dict(os.environ, {"VALUE": "old", "EMPTY": "old", "OMITTED": "keep"}):
        values = await resolve_environment_async(env_files=[base, other, local], env_akv_ref=None, env_akv_strict=True)
        assert values == {"VALUE": "local", "EMPTY": "", "INTERPOLATED": "local/suffix"}
        assert os.environ["VALUE"] == "old"
        with (
            patch("pyrit.setup.initialization.validate_reinitialization_memory", return_value=object()),
            patch.object(CentralMemory, "set_memory_instance"),
        ):
            config = ConfigurationLoader(memory_db_type="in_memory", initialization_scripts=[], env_files=[])
            prepared = await config.preflight_reinitialization_async(environment_values=values)
            prepared = replace(prepared, initializers=())
            await config.apply_prepared_reinitialization_async(prepared=prepared)
        assert os.environ["VALUE"] == "local"
        assert os.environ["EMPTY"] == ""
        assert os.environ["OMITTED"] == "keep"


async def test_key_vault_selection_skips_default_base_and_overrides_process(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("IGNORED=base\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("VALUE=local\n", encoding="utf-8")
    with (
        patch("pyrit.setup.environment_loading.path.CONFIGURATION_DIRECTORY_PATH", tmp_path),
        patch(
            "pyrit.setup.environment_loading._fetch_akv_document_async",
            AsyncMock(return_value=("VALUE=vault\nDERIVED=${VALUE}\n", "https://test.vault.azure.net")),
        ),
        patch.dict(os.environ, {"VALUE": "stale"}),
    ):
        values = await resolve_environment_async(
            env_files=None, env_akv_ref=["https://test.vault.azure.net/secrets/bootstrap"], env_akv_strict=True
        )
    assert values == {"VALUE": "local", "DERIVED": "local"}


@pytest.mark.usefixtures("patch_central_database")
async def test_reload_changed_and_removed_scripts_preserves_memory_and_history(
    tmp_path: Path, sqlite_instance: SQLiteMemory
) -> None:
    from pyrit.models import Message, MessagePiece

    message = Message(
        message_pieces=[MessagePiece(role="user", original_value="history", conversation_id=str(uuid.uuid4()))]
    )
    sqlite_instance.add_message_to_memory(request=message)
    source = tmp_path / "scripts"
    source.mkdir()
    script = source / "custom.py"
    template = """
from pyrit.setup.pyrit_initializer import PyRITInitializer
from pyrit.registry import TargetRegistry
from pyrit.prompt_target import TextTarget
class CustomInitializer(PyRITInitializer):
    @property
    def name(self): return "custom"
    @property
    def description(self): return "test"
    @property
    def required_env_vars(self): return []
    async def initialize_async(self):
        TargetRegistry.get_registry_singleton().instances.register(TextTarget(), name="TARGET_NAME")
"""
    script.write_text(template.replace("TARGET_NAME", "first"), encoding="utf-8")
    config = ConfigurationLoader(
        memory_db_type="in_memory",
        env_files=[],
        initializers=["custom"],
        allow_custom_initializers=True,
        custom_initializers_source=str(source),
    )
    try:
        prepared = await config.preflight_reinitialization_async(environment_values={})
        await config.apply_prepared_reinitialization_async(prepared=prepared)
        assert TargetRegistry.get_registry_singleton().instances.get("first") is not None
        first_registry = TargetRegistry.get_registry_singleton()
        script.write_text(template.replace("TARGET_NAME", "second"), encoding="utf-8")
        prepared = await config.preflight_reinitialization_async(environment_values={})
        await config.apply_prepared_reinitialization_async(prepared=prepared)
        registry = TargetRegistry.get_registry_singleton()
        assert registry is not first_registry
        assert registry.instances.get("first") is None
        assert registry.instances.get("second") is not None
        prepared = await config.preflight_reinitialization_async(environment_values={})
        await config.apply_prepared_reinitialization_async(prepared=prepared)
        assert TargetRegistry.get_registry_singleton().instances.get_names() == ["second"]
        script.unlink()
        with pytest.raises(ValueError, match="not found"):
            await config.preflight_reinitialization_async(environment_values={})
        assert TargetRegistry.get_registry_singleton().instances.get("second") is not None
        assert CentralMemory.get_memory_instance() is sqlite_instance
        assert sqlite_instance.get_message_pieces(conversation_id=message.message_pieces[0].conversation_id)
    finally:
        reset_setup_registries()


@pytest.mark.usefixtures("patch_central_database")
async def test_memory_change_rejected_before_environment_or_registries(tmp_path: Path) -> None:
    registry = TargetRegistry.get_registry_singleton()
    env = tmp_path / ".env"
    env.write_text("DO_NOT_APPLY=changed\n", encoding="utf-8")
    config = ConfigurationLoader(memory_db_type="azure_sql", env_files=[str(env)])
    with patch.dict(os.environ, {"DO_NOT_APPLY": "original"}):
        with pytest.raises(ValueError, match="restart"):
            validate_reinitialization_memory(
                memory_db_type=config._MEMORY_DB_TYPE_MAP[config.memory_db_type],
                environment={"DO_NOT_APPLY": "changed"},
            )
        assert os.environ["DO_NOT_APPLY"] == "original"
        assert TargetRegistry.get_registry_singleton() is registry


def test_missing_memory_can_be_created_after_failed_startup() -> None:
    with patch.object(CentralMemory, "get_memory_instance", side_effect=ValueError("missing")):
        assert validate_reinitialization_memory(memory_db_type="InMemory", environment={}) is None


def test_malformed_default_layer_is_not_silently_ignored(tmp_path: Path) -> None:
    broken = tmp_path / "default.yaml"
    broken.write_text("broken: [", encoding="utf-8")
    with patch("pyrit.setup.configuration_loader.DEFAULT_CONFIG_PATH", broken):
        with pytest.raises(ValueError, match="default configuration"):
            ConfigurationLoader.load_with_overrides(strict=True)


@pytest.mark.parametrize(
    "key",
    [
        AzureSQLMemory.AZURE_SQL_DB_CONNECTION_STRING,
        AzureSQLMemory.AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL,
        AzureSQLMemory.AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN,
    ],
)
def test_azure_connection_and_storage_changes_require_restart(key: str) -> None:
    memory = MagicMock(spec=AzureSQLMemory)
    memory.AZURE_SQL_DB_CONNECTION_STRING = AzureSQLMemory.AZURE_SQL_DB_CONNECTION_STRING
    memory.AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL = AzureSQLMemory.AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL
    memory.AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN = AzureSQLMemory.AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN
    memory._connection_string = "connection"
    memory._results_container_url = "container"
    memory._results_container_sas_token = "secret"
    environment = {
        memory.AZURE_SQL_DB_CONNECTION_STRING: "connection",
        memory.AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL: "container",
        memory.AZURE_STORAGE_ACCOUNT_DB_DATA_SAS_TOKEN: "secret",
    }
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        assert validate_reinitialization_memory(memory_db_type="AzureSQL", environment=environment) is memory
        for value in ("changed-secret", ""):
            with pytest.raises(ValueError, match="restart") as error:
                validate_reinitialization_memory(memory_db_type="AzureSQL", environment={**environment, key: value})
            assert "changed-secret" not in str(error.value)


async def test_replacement_resolves_interpolated_key_vault_references(tmp_path: Path) -> None:
    file = tmp_path / ".env"
    file.write_text(
        "VAULT=https://new.vault.azure.net\nKEY=kv:${VAULT}/secrets/key\nDERIVED=${KEY}/suffix\n",
        encoding="utf-8",
    )
    client = AsyncMock()
    client.get_secret.return_value.value = "resolved"
    client.__aenter__.return_value = client
    credential = AsyncMock()
    with (
        patch("azure.identity.aio.DefaultAzureCredential", return_value=credential),
        patch("pyrit.setup.environment_loading._create_akv_secret_client", return_value=client) as create,
        patch.dict(os.environ, {"VAULT": "https://old.vault.azure.net", "KEY": "old-secret"}),
    ):
        values = await resolve_environment_async(env_files=[file], env_akv_ref=None, env_akv_strict=True)
    assert values["DERIVED"] == "resolved/suffix"
    assert values["KEY"] == "resolved"
    assert create.call_args.kwargs["vault_url"] == "https://new.vault.azure.net"
