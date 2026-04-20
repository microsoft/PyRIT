# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import sys
from unittest.mock import patch

import pytest

from pyrit.common.apply_defaults import reset_default_values
from pyrit.setup.initializers import SimpleInitializer


class TestSimpleInitializer:
    """Tests for SimpleInitializer class - basic functionality."""

    def test_simple_initializer_can_be_created(self):
        """Test that SimpleInitializer can be instantiated."""
        init = SimpleInitializer()
        assert init is not None
        assert init.name == "Simple Complete Configuration"
        assert init.execution_order == 1


@pytest.mark.usefixtures("patch_central_database")
class TestSimpleInitializerInitialize:
    """Tests for SimpleInitializer.initialize method."""

    def setup_method(self) -> None:
        """Set up before each test."""
        reset_default_values()
        # Set up required env vars for OpenAI
        os.environ["OPENAI_CHAT_ENDPOINT"] = "https://test.openai.azure.com"
        os.environ["OPENAI_CHAT_MODEL"] = "gpt-4"
        # Clean up globals
        for attr in ["default_converter_target", "default_objective_scorer", "adversarial_config"]:
            if hasattr(sys.modules["__main__"], attr):
                delattr(sys.modules["__main__"], attr)

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_default_values()
        # Clean up env vars
        for var in ["OPENAI_CHAT_ENDPOINT", "OPENAI_CHAT_KEY", "OPENAI_CHAT_MODEL"]:
            if var in os.environ:
                del os.environ[var]
        # Clean up globals
        for attr in ["default_converter_target", "default_objective_scorer", "adversarial_config"]:
            if hasattr(sys.modules["__main__"], attr):
                delattr(sys.modules["__main__"], attr)

    @pytest.mark.asyncio
    async def test_initialize_with_api_key_runs_without_error(self):
        """Test that initialize runs without errors when API key is provided."""
        os.environ["OPENAI_CHAT_KEY"] = "test_key"
        init = SimpleInitializer()
        await init.initialize_async()

    @pytest.mark.asyncio
    async def test_initialize_with_entra_auth_runs_without_error(self):
        """Test that initialize falls back to Entra auth when no API key is set."""
        init = SimpleInitializer()
        with patch("pyrit.auth.get_azure_openai_auth", return_value="mock_token"):
            await init.initialize_async()

    @pytest.mark.asyncio
    async def test_initialize_non_azure_endpoint_without_key_raises(self):
        """Test that a non-Azure endpoint without an API key raises ValueError."""
        os.environ["OPENAI_CHAT_ENDPOINT"] = "https://api.openai.com/v1"
        init = SimpleInitializer()
        with pytest.raises(ValueError, match="OPENAI_CHAT_KEY environment variable is required"):
            await init.initialize_async()

    @pytest.mark.asyncio
    async def test_get_info_after_initialize_has_populated_data(self):
        """Test that get_info_async() returns populated data after initialization."""
        os.environ["OPENAI_CHAT_KEY"] = "test_key"
        init = SimpleInitializer()
        await init.initialize_async()

        info = await SimpleInitializer.get_info_async()

        # Verify basic structure
        assert isinstance(info, dict)
        assert "name" in info
        assert "default_values" in info
        assert "global_variables" in info

        # Verify default_values list is populated and not empty
        assert isinstance(info["default_values"], list)
        assert len(info["default_values"]) > 0, "default_values should be populated after initialization"

        # Verify expected default values are present
        default_values_str = str(info["default_values"])
        assert "converter_target" in default_values_str
        assert "attack_scoring_config" in default_values_str

        # Verify global_variables list is populated and not empty
        assert isinstance(info["global_variables"], list)
        assert len(info["global_variables"]) > 0, "global_variables should be populated after initialization"

        # Verify expected global variables are present
        assert "default_converter_target" in info["global_variables"]
        assert "default_objective_scorer" in info["global_variables"]
        assert "adversarial_config" in info["global_variables"]


class TestSimpleInitializerGetInfo:
    """Tests for SimpleInitializer.get_info method - basic functionality."""

    async def test_get_info_returns_expected_structure(self):
        """Test that get_info_async() returns expected structure."""
        info = await SimpleInitializer.get_info_async()

        assert isinstance(info, dict)
        assert info["name"] == "Simple Complete Configuration"
        assert info["class"] == "SimpleInitializer"
        assert "required_env_vars" in info
        assert "OPENAI_CHAT_ENDPOINT" in info["required_env_vars"]
