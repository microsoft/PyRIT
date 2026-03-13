# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from unittest.mock import patch

import pytest

from pyrit.prompt_target import (
    OpenAIChatTarget,
    OpenAICompletionTarget,
    OpenAIImageTarget,
    OpenAIResponseTarget,
    OpenAITTSTarget,
    OpenAIVideoTarget,
    RealtimeTarget,
)


@pytest.fixture
def patch_central_database():
    """Mock the central database to avoid database operations in tests."""
    with patch("pyrit.memory.central_memory.CentralMemory.get_memory_instance") as mock_memory:
        mock_instance = mock_memory.return_value
        mock_instance.get_all_embeddings.return_value = []
        mock_instance.get_all_prompt_pieces.return_value = []
        yield mock_memory


class TestURLWarnings:
    """Tests for URL format validation warnings (URLs are never modified)."""

    def test_old_azure_chat_url_warns_unchanged(self, caplog, patch_central_database):
        """Test old Azure chat URL triggers warning but remains unchanged."""
        old_url = "https://test.openai.azure.com/openai/deployments/gpt-4/chat/completions?api-version=2024-02-15"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint=old_url,
                api_key="test-key",
            )

        # Check that URL was NOT converted - kept as-is
        assert target._endpoint == old_url
        assert target._async_client is not None
        assert str(target._async_client.base_url).rstrip("/") == old_url.rstrip("/")

        # Check that warning was logged
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) >= 1  # May have multiple warnings
        assert any("deployment in path" in log.message for log in conversion_warnings)
        assert any("Recommended format:" in log.message for log in conversion_warnings)

    def test_old_azure_completions_url_warns_unchanged(self, caplog, patch_central_database):
        """Test old Azure completions URL triggers warning but remains unchanged."""
        old_url = "https://test.openai.azure.com/openai/deployments/text-davinci-003/completions?api-version=2024-02-15"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            from pyrit.prompt_target import OpenAICompletionTarget

            target = OpenAICompletionTarget(
                endpoint=old_url,
                api_key="test-key",
                model_name="text-davinci-003",
            )

        # Check that URL was NOT converted - kept as-is
        assert target._endpoint == old_url
        assert str(target._async_client.base_url).rstrip("/") == old_url.rstrip("/")

        # Model name should be text-davinci-003
        assert target._model_name == "text-davinci-003"

        # Check warning
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) >= 1

    def test_old_azure_images_url_warns_unchanged(self, caplog, patch_central_database):
        """Test old Azure images URL triggers warning but remains unchanged."""
        old_url = "https://test.openai.azure.com/openai/deployments/dall-e-3/images/generations?api-version=2024-02-15"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            from pyrit.prompt_target import OpenAIImageTarget

            target = OpenAIImageTarget(
                endpoint=old_url,
                api_key="test-key",
                model_name="dall-e-3",
            )

        # Check that URL was NOT converted - kept as-is
        assert target._endpoint == old_url
        assert str(target._async_client.base_url).rstrip("/") == old_url.rstrip("/")

        # Model name should be dall-e-3
        assert target._model_name == "dall-e-3"

        # Check warning
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) >= 1

    def test_old_azure_audio_url_warns_unchanged(self, caplog, patch_central_database):
        """Test old Azure audio/speech URL triggers warning but remains unchanged."""
        old_url = "https://test.openai.azure.com/openai/deployments/tts-1/audio/speech?api-version=2024-02-15"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            from pyrit.prompt_target import OpenAITTSTarget

            target = OpenAITTSTarget(
                endpoint=old_url,
                api_key="test-key",
                model_name="tts-1",
            )

        # Check that URL was NOT converted - kept as-is
        assert target._endpoint == old_url
        assert str(target._async_client.base_url).rstrip("/") == old_url.rstrip("/")

        # Model name should be tts-1
        assert target._model_name == "tts-1"

        # Check warning
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) >= 1

    def test_old_azure_responses_url_warns_unchanged(self, caplog, patch_central_database):
        """Test old Azure responses URL triggers warning but remains unchanged."""
        old_url = "https://test.openai.azure.com/openai/deployments/o1-preview/responses?api-version=2024-09-01"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            from pyrit.prompt_target import OpenAIResponseTarget

            target = OpenAIResponseTarget(
                endpoint=old_url,
                api_key="test-key",
                model_name="o1-preview",
            )

        # Check that URL was NOT converted - kept as-is
        assert target._endpoint == old_url
        assert str(target._async_client.base_url).rstrip("/") == old_url.rstrip("/")

        # Model name should be o1-preview
        assert target._model_name == "o1-preview"

        # Check warning
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) >= 1

    def test_old_azure_videos_url_warns_unchanged(self, caplog, patch_central_database):
        """Test old Azure videos URL triggers warning but remains unchanged."""
        old_url = "https://test.openai.azure.com/openai/deployments/sora-2/videos?api-version=2024-12-01"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            from pyrit.prompt_target import OpenAIVideoTarget

            target = OpenAIVideoTarget(
                endpoint=old_url,
                api_key="test-key",
                model_name="sora-2",
            )

        # Check that URL was NOT converted - kept as-is
        assert target._endpoint == old_url
        assert str(target._async_client.base_url).rstrip("/") == old_url.rstrip("/")

        # Model name should be sora-2
        assert target._model_name == "sora-2"

        # Check warning
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) >= 1

    def test_old_azure_url_without_api_version_warns_unchanged(self, caplog, patch_central_database):
        """Test old Azure URL without api-version parameter triggers warning but remains unchanged."""
        old_url = "https://test.openai.azure.com/openai/deployments/gpt-4/chat/completions"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint=old_url,
                api_key="test-key",
            )

        # Check that URL was NOT converted - kept as-is
        assert target._endpoint == old_url
        assert str(target._async_client.base_url).rstrip("/") == old_url.rstrip("/")

        # Check warning
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) >= 1

    def test_new_azure_url_unchanged_no_warning(self, caplog, patch_central_database):
        """Test new Azure URL format remains unchanged with no warning logged."""
        new_url = "https://test.openai.azure.com/openai/v1"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint=new_url,
                api_key="test-key",
            )

        # URL should remain unchanged
        assert target._endpoint == new_url
        assert str(target._async_client.base_url).rstrip("/") == new_url.rstrip("/")

        # No conversion warning should be logged
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) == 0

    def test_platform_openai_chat_url_warns_about_path(self, caplog, patch_central_database):
        """Test platform OpenAI chat URL triggers warning about API path."""
        platform_url = "https://api.openai.com/v1/chat/completions"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint=platform_url,
                api_key="test-key",
            )

        # URL should remain unchanged
        assert target._endpoint == platform_url
        assert str(target._async_client.base_url).rstrip("/") == platform_url.rstrip("/")

        # Should have warning about API path
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        path_warnings = [log for log in warning_logs if "/chat/completions" in log.message]
        assert len(path_warnings) >= 1

    def test_platform_openai_completion_url_warns_about_path(self, caplog, patch_central_database):
        """Test platform OpenAI completion URL triggers warning about API path."""
        platform_url = "https://api.openai.com/v1/completions"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAICompletionTarget(
                model_name="davinci-002",
                endpoint=platform_url,
                api_key="test-key",
            )

        # URL should remain unchanged
        assert target._endpoint == platform_url
        assert str(target._async_client.base_url).rstrip("/") == platform_url.rstrip("/")

        # Should have warning about API path
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        path_warnings = [log for log in warning_logs if "/completions" in log.message]
        assert len(path_warnings) >= 1

    def test_platform_openai_response_url_warns_about_path(self, caplog, patch_central_database):
        """Test platform OpenAI response URL triggers warning about API path."""
        platform_url = "https://api.openai.com/v1/responses"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIResponseTarget(
                model_name="gpt-4.1",
                endpoint=platform_url,
                api_key="test-key",
            )

        # URL should remain unchanged
        assert target._endpoint == platform_url
        assert str(target._async_client.base_url).rstrip("/") == platform_url.rstrip("/")

        # Should have warning about API path
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        path_warnings = [log for log in warning_logs if "/responses" in log.message]
        assert len(path_warnings) >= 1

    def test_platform_openai_image_url_warns_about_path(self, caplog, patch_central_database):
        """Test platform OpenAI image URL triggers warning about API path."""
        platform_url = "https://api.openai.com/v1/images/generations"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIImageTarget(
                model_name="dall-e-3",
                endpoint=platform_url,
                api_key="test-key",
            )

        # URL should remain unchanged
        assert target._endpoint == platform_url
        assert str(target._async_client.base_url).rstrip("/") == platform_url.rstrip("/")

        # Should have warning about API path
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        path_warnings = [log for log in warning_logs if "/images/generations" in log.message]
        assert len(path_warnings) >= 1

    def test_platform_openai_tts_url_warns_about_path(self, caplog, patch_central_database):
        """Test platform OpenAI TTS URL triggers warning about API path."""
        platform_url = "https://api.openai.com/v1/audio/speech"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAITTSTarget(
                model_name="tts-1",
                endpoint=platform_url,
                api_key="test-key",
            )

        # URL should remain unchanged
        assert target._endpoint == platform_url
        assert str(target._async_client.base_url).rstrip("/") == platform_url.rstrip("/")

        # Should have warning about API path
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        path_warnings = [log for log in warning_logs if "/audio/speech" in log.message]
        assert len(path_warnings) >= 1

    def test_platform_openai_video_url_warns_about_path(self, caplog, patch_central_database):
        """Test platform OpenAI video URL triggers warning about API path."""
        platform_url = "https://api.openai.com/v1/videos"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIVideoTarget(
                model_name="sora-2",
                endpoint=platform_url,
                api_key="test-key",
            )

        # URL should remain unchanged
        assert target._endpoint == platform_url
        assert str(target._async_client.base_url).rstrip("/") == platform_url.rstrip("/")

        # Should have warning about API path
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        path_warnings = [log for log in warning_logs if "/videos" in log.message]
        assert len(path_warnings) >= 1

    def test_azure_foundry_url_unchanged(self, caplog, patch_central_database):
        """Test Azure Foundry URL remains unchanged."""
        foundry_url = "https://my-resource.models.ai.azure.com/chat/completions"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIChatTarget(
                model_name="deepseek-r1",
                endpoint=foundry_url,
                api_key="test-key",
            )

        # URL should remain unchanged
        assert target._endpoint == foundry_url
        assert str(target._async_client.base_url).rstrip("/") == foundry_url.rstrip("/")

        # May have warning about API path in URL
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        # No old Azure format warning
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) == 0

    def test_old_azure_url_with_token_provider_warns_unchanged(self, caplog, patch_central_database):
        """Test old Azure URL with token provider triggers warning but remains unchanged."""
        old_url = "https://test.openai.azure.com/openai/deployments/gpt-4/chat/completions?api-version=2024-02-15"

        def mock_token_provider():
            return "mock-token"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint=old_url,
                api_key=mock_token_provider,
            )

        # Check URL was NOT converted - kept as-is
        assert target._endpoint == old_url
        assert str(target._async_client.base_url).rstrip("/") == old_url.rstrip("/")

        # Check warning was logged
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) >= 1

    def test_warning_message_contains_documentation_link(self, caplog, patch_central_database):
        """Test that warning message includes link to Microsoft documentation."""
        old_url = "https://test.openai.azure.com/openai/deployments/gpt-4/chat/completions?api-version=2024-02-15"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            OpenAIChatTarget(
                model_name="gpt-4",
                endpoint=old_url,
                api_key="test-key",
            )

        # Check warning contains documentation link
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format detected" in log.message]
        assert len(conversion_warnings) == 1
        assert "learn.microsoft.com" in conversion_warnings[0].message
        assert "api-version-deprecation" in conversion_warnings[0].message

    def test_warning_message_contains_deployment_name(self, caplog, patch_central_database):
        """Test that warning message includes the extracted deployment name."""
        old_url = (
            "https://test.openai.azure.com/openai/deployments/my-custom-gpt4/chat/completions?api-version=2024-02-15"
        )

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            OpenAIChatTarget(
                endpoint=old_url,
                api_key="test-key",
                model_name="my-custom-gpt4",
            )

        # Check warning contains deployment name
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) >= 1
        assert any("my-custom-gpt4" in log.message for log in conversion_warnings)


class TestClientTypeUsage:
    """Tests to verify we only use AsyncOpenAI client."""

    def test_async_client_is_asyncopenai_for_old_azure_url(self, patch_central_database):
        """Test that AsyncOpenAI is used even for old Azure URLs."""
        from openai import AsyncOpenAI

        old_url = "https://test.openai.azure.com/openai/deployments/gpt-4/chat/completions?api-version=2024-02-15"

        with patch.dict(os.environ, {}, clear=True):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint=old_url,
                api_key="test-key",
            )

        # Verify we're using AsyncOpenAI, not AsyncAzureOpenAI
        assert isinstance(target._async_client, AsyncOpenAI)
        assert type(target._async_client).__name__ == "AsyncOpenAI"

    def test_async_client_is_asyncopenai_for_new_azure_url(self, patch_central_database):
        """Test that AsyncOpenAI is used for new Azure URLs."""
        from openai import AsyncOpenAI

        new_url = "https://test.openai.azure.com/openai/v1"

        with patch.dict(os.environ, {}, clear=True):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint=new_url,
                api_key="test-key",
            )

        # Verify we're using AsyncOpenAI
        assert isinstance(target._async_client, AsyncOpenAI)

    def test_async_client_is_asyncopenai_for_platform(self, patch_central_database):
        """Test that AsyncOpenAI is used for platform OpenAI."""
        from openai import AsyncOpenAI

        platform_url = "https://api.openai.com/v1/chat/completions"

        with patch.dict(os.environ, {}, clear=True):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint=platform_url,
                api_key="test-key",
            )

        # Verify we're using AsyncOpenAI
        assert isinstance(target._async_client, AsyncOpenAI)


class TestRealtimeURLWarnings:
    """Tests for RealtimeTarget URL validation warnings."""

    def test_old_azure_realtime_url_warns_unchanged(self, caplog, patch_central_database):
        """Test old Azure realtime URL triggers warning but remains unchanged."""
        old_url = "wss://test.openai.azure.com/openai/deployments/gpt-4o-realtime/realtime?api-version=2024-10-01"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = RealtimeTarget(
                model_name="gpt-4o-realtime",
                endpoint=old_url,
                api_key="test-key",
            )

        # Check that URL was NOT converted - kept as-is
        assert target._endpoint == old_url
        assert str(target._async_client.base_url).rstrip("/") == old_url.rstrip("/")
        assert "Old Azure URL format" in caplog.text

    def test_new_azure_realtime_url_with_v1(self, patch_central_database):
        """Test new Azure realtime URL with /openai/v1 is accepted."""
        new_url = "wss://test.openai.azure.com/openai/v1"

        with patch.dict(os.environ, {}, clear=True):
            target = RealtimeTarget(
                model_name="gpt-4o-realtime",
                endpoint=new_url,
                api_key="test-key",
            )

        # URL should remain wss://
        assert target._endpoint == new_url

    def test_https_realtime_url_kept_as_https(self, caplog, patch_central_database):
        """Test https realtime URL is kept as-is (not converted to wss)."""
        url = "https://test.openai.azure.com/openai/v1"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = RealtimeTarget(
                model_name="gpt-4o-realtime",
                endpoint=url,
                api_key="test-key",
            )

        # URL should remain https (not converted to wss)
        assert target._endpoint == url
        # There may be a warning about scheme

    def test_platform_openai_realtime_url_warns_about_path(self, caplog, patch_central_database):
        """Test platform OpenAI realtime URL triggers warning about API path."""
        platform_url = "wss://api.openai.com/v1/realtime"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = RealtimeTarget(
                model_name="gpt-4o-realtime-preview",
                endpoint=platform_url,
                api_key="test-key",
            )

        # Platform URL should remain unchanged
        assert target._endpoint == platform_url
        assert str(target._async_client.base_url).rstrip("/") == platform_url.rstrip("/")
        # Should have warning about /realtime path
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        path_warnings = [log for log in warning_logs if "/realtime" in log.message]
        assert len(path_warnings) >= 1


class TestURLValidation:
    """Tests for URL validation and warning behavior."""

    def test_chat_completions_path_triggers_warning(self, caplog, patch_central_database):
        """Test that /chat/completions path triggers a warning but URL remains unchanged."""
        url = "https://test.openai.azure.com/openai/v1/chat/completions"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint=url,
                api_key="test-key",
            )

        # URL should remain unchanged
        assert target._endpoint == url
        assert str(target._async_client.base_url).rstrip("/") == url.rstrip("/")
        # Should have warning about API path
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        path_warnings = [log for log in warning_logs if "/chat/completions" in log.message]
        assert len(path_warnings) >= 1

    def test_azure_foundry_triggers_warning(self, caplog, patch_central_database):
        """Test that Azure Foundry URL with API path triggers a warning."""
        url = "https://test.models.ai.azure.com/chat/completions"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIChatTarget(
                model_name="deepseek-r1",
                endpoint=url,
                api_key="test-key",
            )

        # URL should remain unchanged
        assert target._endpoint == url
        assert str(target._async_client.base_url).rstrip("/") == url.rstrip("/")

    def test_old_url_warns_unchanged(self, caplog, patch_central_database):
        """Test that old URL triggers warning but remains unchanged."""
        old_url = "https://test.openai.azure.com/openai/deployments/gpt-4/chat/completions?api-version=2024-02-15"

        with patch.dict(os.environ, {}, clear=True), caplog.at_level(logging.WARNING):
            target = OpenAIChatTarget(
                model_name="gpt-4",
                endpoint=old_url,
                api_key="test-key",
            )

        # URL should remain unchanged
        assert target._endpoint == old_url
        assert str(target._async_client.base_url).rstrip("/") == old_url.rstrip("/")
        # Should have warning about old format
        warning_logs = [record for record in caplog.records if record.levelno == logging.WARNING]
        conversion_warnings = [log for log in warning_logs if "Old Azure URL format" in log.message]
        assert len(conversion_warnings) >= 1
