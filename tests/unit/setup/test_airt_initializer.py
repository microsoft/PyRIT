# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import os
import sys
from unittest.mock import patch

import pytest
import yaml

from pyrit.common.apply_defaults import reset_default_values
from pyrit.setup.initializers import AIRTInitializer


@pytest.fixture
def patch_pyrit_conf(tmp_path):
    """Create a temporary .pyrit_conf file and patch DEFAULT_CONFIG_PATH to point to it."""
    conf_file = tmp_path / ".pyrit_conf"
    conf_file.write_text(yaml.dump({"operator": "test_user", "operation": "test_op"}))
    with patch("pyrit.setup.initializers.airt.DEFAULT_CONFIG_PATH", conf_file):
        yield


class TestAIRTInitializer:
    """Tests for AIRTInitializer class - basic functionality."""

    def test_airt_initializer_can_be_created(self):
        """Test that AIRTInitializer can be instantiated."""
        init = AIRTInitializer()
        assert init is not None

    def test_airt_initializer_description(self):
        """Test that AIRTInitializer has the correct description."""
        init = AIRTInitializer()
        assert "AI Red Team" in init.description
        assert "Azure OpenAI" in init.description


@pytest.mark.usefixtures("patch_central_database")
class TestAIRTInitializerInitialize:
    """Tests for AIRTInitializer.initialize method."""

    def setup_method(self) -> None:
        """Set up before each test."""
        reset_default_values()
        # Set up required env vars for AIRT
        os.environ["AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"] = "https://test-converter.openai.azure.com"
        os.environ["AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"] = "gpt-4"
        os.environ["AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT2"] = "https://test-scorer.openai.azure.com"
        os.environ["AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL2"] = "gpt-4"
        os.environ["AZURE_CONTENT_SAFETY_API_ENDPOINT"] = "https://test-safety.cognitiveservices.azure.com"
        os.environ["AZURE_SQL_DB_CONNECTION_STRING"] = "Server=test.database.windows.net;Database=testdb"
        os.environ["AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL"] = "https://teststorage.blob.core.windows.net/data"
        os.environ["GLOBAL_MEMORY_LABELS"] = (
            '{"operation": "test_op", "operator": "test_user", "email": "test@test.com"}'
        )
        # Clean up globals
        for attr in [
            "default_converter_target",
            "default_harm_scorer",
            "default_objective_scorer",
            "adversarial_config",
        ]:
            if hasattr(sys.modules["__main__"], attr):
                delattr(sys.modules["__main__"], attr)

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_default_values()
        # Clean up env vars
        for var in [
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT2",
            "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL2",
            "AZURE_CONTENT_SAFETY_API_ENDPOINT",
            "AZURE_SQL_DB_CONNECTION_STRING",
            "AZURE_STORAGE_ACCOUNT_DB_DATA_CONTAINER_URL",
            "GLOBAL_MEMORY_LABELS",
        ]:
            if var in os.environ:
                del os.environ[var]
        # Clean up globals
        for attr in [
            "default_converter_target",
            "default_harm_scorer",
            "default_objective_scorer",
            "adversarial_config",
        ]:
            if hasattr(sys.modules["__main__"], attr):
                delattr(sys.modules["__main__"], attr)

    async def test_initialize_runs_without_error(self, patch_pyrit_conf):
        """Test that initialize runs without errors when no API keys are set (Entra auth fallback)."""
        init = AIRTInitializer()
        with (
            patch("pyrit.setup.initializers.airt.get_azure_openai_auth", return_value="mock_token"),
            patch("pyrit.setup.initializers.airt.get_azure_token_provider", return_value="mock_token_provider"),
        ):
            await init.initialize_async()

    async def test_initialize_uses_api_keys_when_set(self, patch_pyrit_conf):
        """Test that initialize uses API keys from env vars when they are set."""
        os.environ["AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY"] = "converter-key"
        os.environ["AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY2"] = "scorer-key"
        os.environ["AZURE_CONTENT_SAFETY_API_KEY"] = "safety-key"
        try:
            init = AIRTInitializer()
            with (
                patch("pyrit.setup.initializers.airt.get_azure_openai_auth") as mock_auth,
                patch("pyrit.setup.initializers.airt.get_azure_token_provider") as mock_token,
            ):
                await init.initialize_async()
                # Entra auth should NOT be called when API keys are set
                mock_auth.assert_not_called()
                mock_token.assert_not_called()
        finally:
            for var in [
                "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY",
                "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_KEY2",
                "AZURE_CONTENT_SAFETY_API_KEY",
            ]:
                if var in os.environ:
                    del os.environ[var]

    async def test_get_info_after_initialize_has_populated_data(self, patch_pyrit_conf):
        """Test that get_info_async() returns populated data after initialization."""
        init = AIRTInitializer()
        with (
            patch("pyrit.setup.initializers.airt.get_azure_openai_auth", return_value="mock_token"),
            patch("pyrit.setup.initializers.airt.get_azure_token_provider", return_value="mock_token_provider"),
        ):
            await init.initialize_async()
            # get_info_async re-runs initialize_async internally, so patches must still be active
            info = await AIRTInitializer.get_info_async()

        # Verify basic structure
        assert isinstance(info, dict)
        assert "description" in info
        assert "default_values" in info
        assert "global_variables" in info

        # Verify default_values list is populated and not empty
        assert isinstance(info["default_values"], list)
        assert len(info["default_values"]) > 0, "default_values should be populated after initialization"

        # Verify expected default values are present
        default_values_str = str(info["default_values"])
        assert "PromptConverter.converter_target" in default_values_str
        assert "PromptSendingAttack.attack_scoring_config" in default_values_str
        assert "PromptSendingAttack.attack_adversarial_config" in default_values_str

        # Verify global_variables list is populated and not empty
        assert isinstance(info["global_variables"], list)
        assert len(info["global_variables"]) > 0, "global_variables should be populated after initialization"

        # Verify expected global variables are present
        assert "default_converter_target" in info["global_variables"]
        assert "default_harm_scorer" in info["global_variables"]
        assert "default_objective_scorer" in info["global_variables"]
        assert "adversarial_config" in info["global_variables"]

    def test_validate_missing_env_vars_raises_error(self):
        """Test that validate raises error when required env vars are missing."""
        # Remove one required env var
        del os.environ["AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"]

        init = AIRTInitializer()
        with pytest.raises(ValueError) as exc_info:
            init.validate()

        error_message = str(exc_info.value)
        assert "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT" in error_message
        assert "environment variables" in error_message

    def test_validate_missing_multiple_env_vars_raises_error(self):
        """Test that validate raises error listing all missing env vars."""
        # Remove multiple required env vars
        del os.environ["AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT"]
        del os.environ["AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL"]

        init = AIRTInitializer()
        with pytest.raises(ValueError) as exc_info:
            init.validate()

        error_message = str(exc_info.value)
        assert "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT" in error_message
        assert "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL" in error_message

    def test_validate_missing_operator_raises_error(self, tmp_path):
        """Test that _validate_operation_fields raises error when operator is missing from .pyrit_conf."""
        conf_file = tmp_path / ".pyrit_conf"
        conf_file.write_text(yaml.dump({"operation": "test_op"}))
        init = AIRTInitializer()
        with (
            patch("pyrit.setup.initializers.airt.DEFAULT_CONFIG_PATH", conf_file),
            pytest.raises(ValueError, match="operator"),
        ):
            init._validate_operation_fields()

    def test_validate_missing_operation_raises_error(self, tmp_path):
        """Test that _validate_operation_fields raises error when operation is missing from .pyrit_conf."""
        conf_file = tmp_path / ".pyrit_conf"
        conf_file.write_text(yaml.dump({"operator": "test_user"}))
        init = AIRTInitializer()
        with (
            patch("pyrit.setup.initializers.airt.DEFAULT_CONFIG_PATH", conf_file),
            pytest.raises(ValueError, match="operation"),
        ):
            init._validate_operation_fields()

    def test_validate_operation_fields_skips_when_pyrit_conf_missing(self, tmp_path):
        """Test that _validate_operation_fields does not crash when .pyrit_conf is missing.

        In container/GUI deployments, .pyrit_conf does not exist. The method should
        skip validation gracefully instead of raising FileNotFoundError.
        """
        nonexistent_path = tmp_path / "nonexistent" / ".pyrit_conf"
        init = AIRTInitializer()
        with patch("pyrit.setup.initializers.airt.DEFAULT_CONFIG_PATH", nonexistent_path):
            # Should not raise
            init._validate_operation_fields()

    def test_validate_operation_fields_preserves_existing_labels_when_pyrit_conf_missing(self, tmp_path):
        """Test that existing GLOBAL_MEMORY_LABELS are preserved when .pyrit_conf is missing."""
        nonexistent_path = tmp_path / "nonexistent" / ".pyrit_conf"
        init = AIRTInitializer()
        with (
            patch("pyrit.setup.initializers.airt.DEFAULT_CONFIG_PATH", nonexistent_path),
            patch.dict("os.environ", {"GLOBAL_MEMORY_LABELS": '{"operator": "gui_user", "operation": "gui_op"}'}),
        ):
            init._validate_operation_fields()
            # Existing labels should remain untouched
            labels = json.loads(os.environ["GLOBAL_MEMORY_LABELS"])
            assert labels["operator"] == "gui_user"
            assert labels["operation"] == "gui_op"

    def test_validate_operation_fields_merges_conf_into_labels(self, tmp_path):
        """Test that .pyrit_conf values are merged into GLOBAL_MEMORY_LABELS when labels are missing."""
        conf_file = tmp_path / ".pyrit_conf"
        conf_file.write_text(yaml.dump({"operator": "conf_user", "operation": "conf_op"}))
        init = AIRTInitializer()
        with (
            patch("pyrit.setup.initializers.airt.DEFAULT_CONFIG_PATH", conf_file),
            patch.dict("os.environ", {}, clear=False),
        ):
            # Remove GLOBAL_MEMORY_LABELS if present
            os.environ.pop("GLOBAL_MEMORY_LABELS", None)
            init._validate_operation_fields()
            labels = json.loads(os.environ["GLOBAL_MEMORY_LABELS"])
            assert labels["operator"] == "conf_user"
            assert labels["operation"] == "conf_op"

    def test_validate_operation_fields_does_not_overwrite_existing_labels(self, tmp_path):
        """Test that .pyrit_conf values do not overwrite existing GLOBAL_MEMORY_LABELS entries."""
        conf_file = tmp_path / ".pyrit_conf"
        conf_file.write_text(yaml.dump({"operator": "conf_user", "operation": "conf_op"}))
        init = AIRTInitializer()
        with (
            patch("pyrit.setup.initializers.airt.DEFAULT_CONFIG_PATH", conf_file),
            patch.dict(
                "os.environ",
                {"GLOBAL_MEMORY_LABELS": '{"operator": "existing_user", "operation": "existing_op"}'},
            ),
        ):
            init._validate_operation_fields()
            labels = json.loads(os.environ["GLOBAL_MEMORY_LABELS"])
            assert labels["operator"] == "existing_user"
            assert labels["operation"] == "existing_op"

    def test_validate_db_connection_raises_error(self):
        """Test that validate raises error when AZURE_SQL_DB_CONNECTION_STRING is missing."""
        del os.environ["AZURE_SQL_DB_CONNECTION_STRING"]
        init = AIRTInitializer()
        with pytest.raises(ValueError) as exc_info:
            init.validate()

        error_message = str(exc_info.value)
        assert "AZURE_SQL_DB_CONNECTION_STRING" in error_message


class TestAIRTInitializerGetInfo:
    """Tests for AIRTInitializer.get_info method - basic functionality."""

    async def test_get_info_returns_expected_structure(self):
        """Test that get_info_async returns expected structure."""
        info = await AIRTInitializer.get_info_async()

        assert isinstance(info, dict)
        assert info["class"] == "AIRTInitializer"
        assert "required_env_vars" in info
        assert "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT" in info["required_env_vars"]
        assert "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT2" in info["required_env_vars"]
        assert "AZURE_CONTENT_SAFETY_API_ENDPOINT" in info["required_env_vars"]

    async def test_get_info_includes_description(self):
        """Test that get_info_async includes the description field."""
        info = await AIRTInitializer.get_info_async()

        assert "description" in info
        assert isinstance(info["description"], str)
        assert len(info["description"]) > 0


async def test_initialize_async_raises_when_converter_endpoint_is_none():
    """Test that initialize_async raises ValueError when converter_endpoint env var is None."""
    init = AIRTInitializer()
    with (
        patch.object(init, "_validate_operation_fields"),
        patch.dict(
            "os.environ",
            {
                "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT2": "https://test.openai.azure.com",
                "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL2": "gpt-4",
            },
            clear=False,
        ),
        patch.dict("os.environ", {"AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": ""}, clear=False),
    ):
        # Remove the key to force None
        os.environ.pop("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT", None)
        with pytest.raises(ValueError, match="converter_endpoint is not initialized"):
            await init.initialize_async()


async def test_initialize_async_raises_when_scorer_endpoint_is_none():
    """Test that initialize_async raises ValueError when scorer_endpoint env var is None."""
    init = AIRTInitializer()
    with (
        patch.object(init, "_validate_operation_fields"),
        patch.dict(
            "os.environ",
            {
                "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT": "https://test.openai.azure.com",
                "AZURE_OPENAI_GPT4O_UNSAFE_CHAT_MODEL": "gpt-4",
            },
            clear=False,
        ),
    ):
        os.environ.pop("AZURE_OPENAI_GPT4O_UNSAFE_CHAT_ENDPOINT2", None)
        with pytest.raises(ValueError, match="scorer_endpoint is not initialized"):
            await init.initialize_async()
