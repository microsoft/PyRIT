# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend target service.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from pyrit.backend.models.targets import CreateTargetRequest
from pyrit.backend.services.target_service import TargetService, get_target_service
from pyrit.models import ComponentIdentifier
from pyrit.registry.object_registries import TargetRegistry


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the TargetRegistry singleton before each test."""
    TargetRegistry.reset_instance()
    yield
    TargetRegistry.reset_instance()


def _mock_target_identifier(*, class_name: str = "MockTarget", **kwargs) -> ComponentIdentifier:
    """Create a mock target identifier using ComponentIdentifier."""
    params = {
        "endpoint": kwargs.get("endpoint"),
        "model_name": kwargs.get("model_name"),
        "temperature": kwargs.get("temperature"),
        "top_p": kwargs.get("top_p"),
        "max_requests_per_minute": kwargs.get("max_requests_per_minute"),
    }
    # Filter out None values to match ComponentIdentifier.of behavior
    clean_params = {k: v for k, v in params.items() if v is not None}
    return ComponentIdentifier(
        class_name=class_name,
        class_module="tests.unit.backend.test_target_service",
        params=clean_params,
    )


async def _test_token_provider() -> str:
    """Shared async token provider used in Entra authentication tests."""
    return "test-token"


class TestListTargets:
    """Tests for TargetService.list_targets method."""

    async def test_list_targets_returns_empty_when_no_targets(self) -> None:
        """Test that list_targets returns empty list when no targets exist."""
        service = TargetService()

        result = await service.list_targets_async()

        assert result.items == []
        assert result.pagination.has_more is False

    async def test_list_targets_returns_targets_from_registry(self) -> None:
        """Test that list_targets returns targets from registry."""
        service = TargetService()

        # Register a mock target
        mock_target = MagicMock()
        mock_target.get_identifier.return_value = _mock_target_identifier(endpoint="http://test")
        service._registry.register_instance(mock_target, name="target-1")

        result = await service.list_targets_async()

        assert len(result.items) == 1
        assert result.items[0].target_registry_name == "target-1"
        assert result.items[0].target_type == "MockTarget"
        assert result.pagination.has_more is False

    async def test_list_targets_paginates_with_limit(self) -> None:
        """Test that list_targets respects the limit parameter."""
        service = TargetService()

        for i in range(5):
            mock_target = MagicMock()
            mock_target.get_identifier.return_value = _mock_target_identifier()
            service._registry.register_instance(mock_target, name=f"target-{i}")

        result = await service.list_targets_async(limit=3)

        assert len(result.items) == 3
        assert result.pagination.limit == 3
        assert result.pagination.has_more is True
        assert result.pagination.next_cursor == result.items[-1].target_registry_name

    async def test_list_targets_cursor_returns_next_page(self) -> None:
        """Test that list_targets cursor skips to the correct position."""
        service = TargetService()

        for i in range(5):
            mock_target = MagicMock()
            mock_target.get_identifier.return_value = _mock_target_identifier()
            service._registry.register_instance(mock_target, name=f"target-{i}")

        first_page = await service.list_targets_async(limit=2)
        second_page = await service.list_targets_async(limit=2, cursor=first_page.pagination.next_cursor)

        assert len(second_page.items) == 2
        assert second_page.items[0].target_registry_name != first_page.items[0].target_registry_name
        assert second_page.pagination.has_more is True

    async def test_list_targets_last_page_has_no_more(self) -> None:
        """Test that the last page has has_more=False and no next_cursor."""
        service = TargetService()

        for i in range(3):
            mock_target = MagicMock()
            mock_target.get_identifier.return_value = _mock_target_identifier()
            service._registry.register_instance(mock_target, name=f"target-{i}")

        first_page = await service.list_targets_async(limit=2)
        last_page = await service.list_targets_async(limit=2, cursor=first_page.pagination.next_cursor)

        assert len(last_page.items) == 1
        assert last_page.pagination.has_more is False
        assert last_page.pagination.next_cursor is None


class TestGetTarget:
    """Tests for TargetService.get_target method."""

    async def test_get_target_returns_none_for_nonexistent(self) -> None:
        """Test that get_target returns None for non-existent target."""
        service = TargetService()

        result = await service.get_target_async(target_registry_name="nonexistent-id")

        assert result is None

    async def test_get_target_returns_target_from_registry(self) -> None:
        """Test that get_target returns target built from registry object."""
        service = TargetService()

        mock_target = MagicMock()
        mock_target.get_identifier.return_value = _mock_target_identifier()
        service._registry.register_instance(mock_target, name="target-1")

        result = await service.get_target_async(target_registry_name="target-1")

        assert result is not None
        assert result.target_registry_name == "target-1"
        assert result.target_type == "MockTarget"

    async def test_list_targets_includes_extra_params_in_target_specific(self) -> None:
        """Test that extra identifier params (reasoning_effort etc.) appear in target_specific_params."""
        service = TargetService()

        mock_target = MagicMock()
        identifier = ComponentIdentifier(
            class_name="OpenAIResponseTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://api.openai.com",
                "model_name": "o3",
                "temperature": 1.0,
                "reasoning_effort": "high",
                "reasoning_summary": "auto",
                "max_output_tokens": 4096,
            },
        )
        mock_target.get_identifier.return_value = identifier
        service._registry.register_instance(mock_target, name="response-target")

        result = await service.list_targets_async()

        assert len(result.items) == 1
        target = result.items[0]
        assert target.temperature == 1.0
        assert target.target_specific_params is not None
        assert target.target_specific_params["reasoning_effort"] == "high"
        assert target.target_specific_params["reasoning_summary"] == "auto"
        assert target.target_specific_params["max_output_tokens"] == 4096

    async def test_get_target_includes_extra_params_in_target_specific(self) -> None:
        """Test that get_target returns target_specific_params with extra identifier params."""
        service = TargetService()

        mock_target = MagicMock()
        identifier = ComponentIdentifier(
            class_name="OpenAIChatTarget",
            class_module="pyrit.prompt_target",
            params={
                "endpoint": "https://api.openai.com",
                "model_name": "gpt-4",
                "frequency_penalty": 0.5,
                "seed": 42,
            },
        )
        mock_target.get_identifier.return_value = identifier
        service._registry.register_instance(mock_target, name="chat-target")

        result = await service.get_target_async(target_registry_name="chat-target")

        assert result is not None
        assert result.target_specific_params is not None
        assert result.target_specific_params["frequency_penalty"] == 0.5
        assert result.target_specific_params["seed"] == 42


class TestGetTargetObject:
    """Tests for TargetService.get_target_object method."""

    def test_get_target_object_returns_none_for_nonexistent(self) -> None:
        """Test that get_target_object returns None for non-existent target."""
        service = TargetService()

        result = service.get_target_object(target_registry_name="nonexistent-id")

        assert result is None

    def test_get_target_object_returns_object_from_registry(self) -> None:
        """Test that get_target_object returns the actual target object."""
        service = TargetService()
        mock_target = MagicMock()
        service._registry.register_instance(mock_target, name="target-1")

        result = service.get_target_object(target_registry_name="target-1")

        assert result is mock_target


class TestCreateTarget:
    """Tests for TargetService.create_target method."""

    async def test_create_target_raises_for_invalid_type(self) -> None:
        """Test that create_target raises for invalid target type."""
        service = TargetService()

        request = CreateTargetRequest(
            type="NonExistentTarget",
            params={},
        )

        with pytest.raises(ValueError, match="not found"):
            await service.create_target_async(request=request)

    async def test_create_target_success(self, sqlite_instance) -> None:
        """Test successful target creation."""
        service = TargetService()

        request = CreateTargetRequest(
            type="TextTarget",
            params={},
        )

        result = await service.create_target_async(request=request)

        assert result.target_registry_name is not None
        assert result.target_type == "TextTarget"

    async def test_create_target_registers_in_registry(self, sqlite_instance) -> None:
        """Test that create_target registers object in registry."""
        service = TargetService()

        request = CreateTargetRequest(
            type="TextTarget",
            params={},
        )

        result = await service.create_target_async(request=request)

        # Object should be retrievable from registry
        target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
        assert target_obj is not None

    async def test_create_target_model_name_not_overridden_by_env_var(self, sqlite_instance) -> None:
        """Test that explicit model_name is not overridden by underlying_model env var."""
        with patch.dict(os.environ, {"OPENAI_CHAT_UNDERLYING_MODEL": "gpt-4o"}):
            service = TargetService()

            request = CreateTargetRequest(
                type="OpenAIChatTarget",
                params={
                    "model_name": "claude-sonnet-4-6",
                    "endpoint": "https://test.openai.azure.com/",
                    "api_key": "test-key",
                },
            )

            result = await service.create_target_async(request=request)

            assert result.model_name == "claude-sonnet-4-6"
            # underlying_model_name should be None since no underlying_model was passed
            assert result.underlying_model_name is None

    async def test_create_target_with_different_underlying_model(self, sqlite_instance) -> None:
        """Test that explicit underlying_model is used when it differs from model_name."""
        service = TargetService()

        request = CreateTargetRequest(
            type="OpenAIChatTarget",
            params={
                "model_name": "my-gpt4o-deployment",
                "endpoint": "https://test.openai.azure.com/",
                "api_key": "test-key",
                "underlying_model": "gpt-4o",
            },
        )

        result = await service.create_target_async(request=request)

        assert result.model_name == "my-gpt4o-deployment"
        assert result.underlying_model_name == "gpt-4o"


class TestCreateTargetEntraAuth:
    """Test that creating targets with Entra auth mode properly authenticates and handles edge cases."""

    async def test_create_openai_target_with_entra_injects_token_provider(self, sqlite_instance) -> None:
        """Entra auth path: api_key is replaced with the authentication callable"""

        with patch(
            "pyrit.backend.services.target_service.get_azure_openai_auth",
            return_value=_test_token_provider,
        ) as mock_get_auth:
            service = TargetService()

            request = CreateTargetRequest(
                type="OpenAIChatTarget",
                params={
                    "endpoint": "https://test.openai.azure.com/",
                    "model_name": "gpt-4o",
                },
                auth_mode="entra",
            )

            result = await service.create_target_async(request=request)

            mock_get_auth.assert_called_once_with("https://test.openai.azure.com/")
            target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
            assert target_obj is not None
            # OpenAI target preserves async callables verbatim through ensure_async_token_provider.
            assert target_obj._api_key is _test_token_provider  # type: ignore[attr-defined]

    async def test_create_openai_target_with_entra_drops_user_api_key(self, sqlite_instance) -> None:
        """Any api_key supplied alongside auth_mode='entra' must be discarded."""

        with patch(
            "pyrit.backend.services.target_service.get_azure_openai_auth",
            return_value=_test_token_provider,
        ):
            service = TargetService()

            request = CreateTargetRequest(
                type="OpenAIChatTarget",
                params={
                    "endpoint": "https://test.openai.azure.com/",
                    "model_name": "gpt-4o",
                    "api_key": "should-be-ignored",
                },
                auth_mode="entra",
            )

            result = await service.create_target_async(request=request)

            target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
            assert target_obj is not None
            assert target_obj._api_key is _test_token_provider  # type: ignore[attr-defined]
            # The literal "should-be-ignored" string must never appear.
            assert target_obj._api_key != "should-be-ignored"  # type: ignore[attr-defined]

    async def test_create_openai_target_with_entra_does_not_mutate_request_params(self, sqlite_instance) -> None:
        """The CreateTargetRequest.params object must remain unchanged after creation."""

        with patch(
            "pyrit.backend.services.target_service.get_azure_openai_auth",
            return_value=_test_token_provider,
        ):
            service = TargetService()

            original_params = {
                "endpoint": "https://test.openai.azure.com/",
                "model_name": "gpt-4o",
                "api_key": "original-key",
            }
            request = CreateTargetRequest(
                type="OpenAIChatTarget",
                params=dict(original_params),
                auth_mode="entra",
            )

            await service.create_target_async(request=request)

            # The caller's request.params must be unchanged after the call.
            assert request.params == original_params

    async def test_create_openai_target_with_entra_non_azure_endpoint_raises(self, sqlite_instance) -> None:
        """Entra ID requires a known Azure OpenAI / AI Foundry hostname suffix."""
        service = TargetService()

        request = CreateTargetRequest(
            type="OpenAIChatTarget",
            params={"endpoint": "https://api.openai.com/"},
            auth_mode="entra",
        )

        with pytest.raises(ValueError, match="Azure endpoint"):
            await service.create_target_async(request=request)

    async def test_create_openai_target_with_entra_substring_lookalike_endpoint_raises(self, sqlite_instance) -> None:
        """Substring 'azure' in the hostname must not be enough to pass Entra validation."""
        service = TargetService()

        request = CreateTargetRequest(
            type="OpenAIChatTarget",
            # Hostname contains 'azure' but does NOT end with an approved suffix.
            params={"endpoint": "https://evil-azure.example.com/"},
            auth_mode="entra",
        )

        with pytest.raises(ValueError, match="Azure endpoint"):
            await service.create_target_async(request=request)

    async def test_create_openai_target_with_entra_missing_endpoint_raises(self, sqlite_instance) -> None:
        """Entra ID for OpenAI must reject a missing endpoint with a clear error."""
        service = TargetService()

        request = CreateTargetRequest(
            type="OpenAIChatTarget",
            params={},
            auth_mode="entra",
        )

        with pytest.raises(ValueError, match="endpoint"):
            await service.create_target_async(request=request)

    async def test_create_azureml_target_with_entra_injects_token_provider(self, sqlite_instance) -> None:
        """AzureML Entra path: api_key is replaced with the ML scope token provider."""

        with patch(
            "pyrit.backend.services.target_service.get_azure_async_token_provider",
            return_value=_test_token_provider,
        ) as mock_get_provider:
            service = TargetService()

            request = CreateTargetRequest(
                type="AzureMLChatTarget",
                params={"endpoint": "https://my-aml.region.inference.ml.azure.com/score"},
                auth_mode="entra",
            )

            result = await service.create_target_async(request=request)

            mock_get_provider.assert_called_once_with("https://ml.azure.com/.default")
            target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
            assert target_obj is not None
            # AzureMLChatTarget stores the provider on _api_key_provider; static _api_key is cleared.
            assert target_obj._api_key_provider is _test_token_provider  # type: ignore[attr-defined]
            assert target_obj._api_key == ""  # type: ignore[attr-defined]

    async def test_create_azureml_target_with_entra_non_aml_endpoint_raises(self, sqlite_instance) -> None:
        """Entra ID for AzureMLChatTarget requires a known AML hostname suffix."""
        service = TargetService()

        request = CreateTargetRequest(
            type="AzureMLChatTarget",
            params={"endpoint": "https://example.com/score"},
            auth_mode="entra",
        )

        with pytest.raises(ValueError, match="AML endpoint"):
            await service.create_target_async(request=request)

    async def test_create_azureml_target_with_entra_substring_lookalike_endpoint_raises(self, sqlite_instance) -> None:
        """Substring 'inference.ml.azure.com' in the hostname must not be enough to pass AML validation."""
        service = TargetService()

        request = CreateTargetRequest(
            type="AzureMLChatTarget",
            # Hostname contains the AML suffix as a substring but does NOT end with it.
            params={"endpoint": "https://evil-inference.ml.azure.com.attacker.com/score"},
            auth_mode="entra",
        )

        with pytest.raises(ValueError, match="AML endpoint"):
            await service.create_target_async(request=request)

    async def test_create_azureml_target_with_entra_missing_endpoint_raises(self, sqlite_instance) -> None:
        """Entra ID for AzureMLChatTarget must reject a missing endpoint with a clear error."""
        service = TargetService()

        request = CreateTargetRequest(
            type="AzureMLChatTarget",
            params={},
            auth_mode="entra",
        )

        with pytest.raises(ValueError, match="endpoint"):
            await service.create_target_async(request=request)

    async def test_create_target_entra_unsupported_type_raises(self, sqlite_instance) -> None:
        """Entra ID is only supported for OpenAI-family and AzureMLChatTarget."""
        service = TargetService()

        request = CreateTargetRequest(
            type="TextTarget",
            params={},
            auth_mode="entra",
        )

        with pytest.raises(ValueError, match="does not support Entra"):
            await service.create_target_async(request=request)


class TestCreateTargetApiKeyAuth:
    """Test that auth_mode='api_key' strictly requires a key in params or environment."""

    async def test_create_openai_target_api_key_mode_without_key_raises(self, sqlite_instance) -> None:
        """Without an api_key (params or env), OpenAITarget would silently fall back to Entra;
        the service must reject this so the user's explicit choice is honored."""
        service = TargetService()

        request = CreateTargetRequest(
            type="OpenAIChatTarget",
            params={
                "model_name": "gpt-4o",
                "endpoint": "https://test.openai.azure.com/",
            },
            auth_mode="api_key",
        )

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_CHAT_KEY", None)
            with pytest.raises(ValueError, match="auth_mode='api_key' requires an API key"):
                await service.create_target_async(request=request)

    async def test_create_openai_target_api_key_mode_with_env_var_succeeds(self, sqlite_instance) -> None:
        """An env-var-supplied key satisfies the api_key requirement."""
        service = TargetService()

        request = CreateTargetRequest(
            type="OpenAIChatTarget",
            params={
                "model_name": "gpt-4o",
                "endpoint": "https://test.openai.azure.com/",
            },
            auth_mode="api_key",
        )

        with patch.dict(os.environ, {"OPENAI_CHAT_KEY": "env-test-key"}):
            result = await service.create_target_async(request=request)

        assert result.target_type == "OpenAIChatTarget"

    async def test_create_openai_target_api_key_mode_rejects_empty_key(self, sqlite_instance) -> None:
        """An empty-string api_key counts as missing and must be rejected."""
        service = TargetService()

        request = CreateTargetRequest(
            type="OpenAIChatTarget",
            params={
                "model_name": "gpt-4o",
                "endpoint": "https://test.openai.azure.com/",
                "api_key": "",
            },
            auth_mode="api_key",
        )

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_CHAT_KEY", None)
            with pytest.raises(ValueError, match="auth_mode='api_key' requires an API key"):
                await service.create_target_async(request=request)

    async def test_create_azureml_target_api_key_mode_without_key_raises(self, sqlite_instance) -> None:
        """AzureMLChatTarget in api_key mode also requires an explicit key."""
        service = TargetService()

        request = CreateTargetRequest(
            type="AzureMLChatTarget",
            params={"endpoint": "https://my-endpoint.eastus.inference.ml.azure.com/score"},
            auth_mode="api_key",
        )

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AZURE_ML_KEY", None)
            with pytest.raises(ValueError, match="auth_mode='api_key' requires an API key"):
                await service.create_target_async(request=request)

    async def test_create_text_target_api_key_mode_skips_validation(self, sqlite_instance) -> None:
        """Targets without an api_key_environment_variable (e.g. TextTarget) are unaffected."""
        service = TargetService()

        request = CreateTargetRequest(
            type="TextTarget",
            params={},
            auth_mode="api_key",
        )

        result = await service.create_target_async(request=request)
        assert result.target_type == "TextTarget"


class TestCreateRoundRobinTarget:
    """Tests for creating RoundRobinTarget via the service."""

    async def test_create_round_robin_target_resolves_registry_names(self, sqlite_instance) -> None:
        """RoundRobinTarget creation resolves registry names to live target objects."""
        service = TargetService()

        # Register two mock targets in the registry to serve as inner targets.
        # We mock the RoundRobinTarget constructor because it does deep validation
        # (same class, multi-turn, editable history) that requires real compatible
        # targets. The service's job is to resolve registry names and pass them
        # through — the constructor validation is tested in RoundRobinTarget's own tests.
        mock_a = MagicMock()
        mock_a.get_identifier.return_value = _mock_target_identifier(
            class_name="OpenAIChatTarget", endpoint="https://a.openai.azure.com", model_name="gpt-4o"
        )
        mock_b = MagicMock()
        mock_b.get_identifier.return_value = _mock_target_identifier(
            class_name="OpenAIChatTarget", endpoint="https://b.openai.azure.com", model_name="gpt-4o"
        )
        service._registry.register_instance(mock_a, name="target-a")
        service._registry.register_instance(mock_b, name="target-b")

        # Patch RoundRobinTarget so the constructor returns a mock that behaves
        # like a registered target (has get_identifier, capabilities, etc.)
        mock_rr = MagicMock()
        mock_rr.get_identifier.return_value = ComponentIdentifier(
            class_name="RoundRobinTarget",
            class_module="pyrit.prompt_target.round_robin_target",
            params={"weights": [2, 1]},
        )
        mock_rr._targets = [mock_a, mock_b]

        with patch(
            "pyrit.backend.services.target_service.RoundRobinTarget",
            return_value=mock_rr,
        ) as mock_rr_cls:
            rr_request = CreateTargetRequest(
                type="RoundRobinTarget",
                params={
                    "target_registry_names": ["target-a", "target-b"],
                    "weights": [2, 1],
                },
            )

            result = await service.create_target_async(request=rr_request)

            # Verify the constructor was called with the resolved targets and weights
            mock_rr_cls.assert_called_once_with(targets=[mock_a, mock_b], weights=[2, 1])
            assert result.target_type == "RoundRobinTarget"

    async def test_create_round_robin_target_fewer_than_2_raises(self, sqlite_instance) -> None:
        """RoundRobinTarget with fewer than 2 registry names raises ValueError."""
        service = TargetService()

        rr_request = CreateTargetRequest(
            type="RoundRobinTarget",
            params={"target_registry_names": ["only-one"]},
        )

        with pytest.raises(ValueError, match="at least 2"):
            await service.create_target_async(request=rr_request)

    async def test_create_round_robin_target_unknown_name_raises(self, sqlite_instance) -> None:
        """RoundRobinTarget with a non-existent registry name raises ValueError."""
        service = TargetService()

        rr_request = CreateTargetRequest(
            type="RoundRobinTarget",
            params={"target_registry_names": ["does-not-exist-a", "does-not-exist-b"]},
        )

        with pytest.raises(ValueError, match="not found"):
            await service.create_target_async(request=rr_request)

    async def test_create_round_robin_target_deduplicates_identical_targets(self, sqlite_instance) -> None:
        """Targets that resolve to the same identifier hash are deduplicated, and
        the corresponding weights are dropped alongside them."""
        service = TargetService()

        # mock_a and mock_a_alias share the same identifier params, so their
        # ComponentIdentifier.hash is identical — they should dedupe to one entry.
        identifier_a = _mock_target_identifier(
            class_name="OpenAIChatTarget", endpoint="https://a.openai.azure.com", model_name="gpt-4o"
        )
        mock_a = MagicMock()
        mock_a.get_identifier.return_value = identifier_a
        mock_a_alias = MagicMock()
        mock_a_alias.get_identifier.return_value = identifier_a

        mock_b = MagicMock()
        mock_b.get_identifier.return_value = _mock_target_identifier(
            class_name="OpenAIChatTarget", endpoint="https://b.openai.azure.com", model_name="gpt-4o"
        )

        service._registry.register_instance(mock_a, name="target-a")
        service._registry.register_instance(mock_a_alias, name="target-a-alias")
        service._registry.register_instance(mock_b, name="target-b")

        mock_rr = MagicMock()
        mock_rr.get_identifier.return_value = ComponentIdentifier(
            class_name="RoundRobinTarget",
            class_module="pyrit.prompt_target.round_robin_target",
            params={"weights": [3, 1]},
        )
        mock_rr._targets = [mock_a, mock_b]

        with patch(
            "pyrit.backend.services.target_service.RoundRobinTarget",
            return_value=mock_rr,
        ) as mock_rr_cls:
            rr_request = CreateTargetRequest(
                type="RoundRobinTarget",
                params={
                    "target_registry_names": ["target-a", "target-a-alias", "target-b"],
                    "weights": [3, 2, 1],
                },
            )

            await service.create_target_async(request=rr_request)

            # The duplicate alias and its weight (2) should be dropped.
            mock_rr_cls.assert_called_once_with(targets=[mock_a, mock_b], weights=[3, 1])

    async def test_create_round_robin_target_all_duplicates_raises(self, sqlite_instance) -> None:
        """If dedup leaves fewer than 2 distinct targets, raise a clear error."""
        service = TargetService()

        identifier = _mock_target_identifier(
            class_name="OpenAIChatTarget", endpoint="https://a.openai.azure.com", model_name="gpt-4o"
        )
        mock_a = MagicMock()
        mock_a.get_identifier.return_value = identifier
        mock_a_alias = MagicMock()
        mock_a_alias.get_identifier.return_value = identifier

        service._registry.register_instance(mock_a, name="target-a")
        service._registry.register_instance(mock_a_alias, name="target-a-alias")

        rr_request = CreateTargetRequest(
            type="RoundRobinTarget",
            params={"target_registry_names": ["target-a", "target-a-alias"]},
        )

        with pytest.raises(ValueError, match="at least 2 distinct targets"):
            await service.create_target_async(request=rr_request)

    async def test_create_round_robin_target_weights_length_mismatch_raises(self, sqlite_instance) -> None:
        """Mismatched weights length raises before any registry lookups."""
        service = TargetService()

        rr_request = CreateTargetRequest(
            type="RoundRobinTarget",
            params={
                "target_registry_names": ["a", "b", "c"],
                "weights": [1, 2],
            },
        )

        with pytest.raises(ValueError, match="weights length"):
            await service.create_target_async(request=rr_request)


class TestTargetServiceSingleton:
    """Tests for get_target_service singleton function."""

    def test_get_target_service_returns_target_service(self) -> None:
        """Test that get_target_service returns a TargetService instance."""
        get_target_service.cache_clear()

        service = get_target_service()
        assert isinstance(service, TargetService)

    def test_get_target_service_returns_same_instance(self) -> None:
        """Test that get_target_service returns the same instance."""
        get_target_service.cache_clear()

        service1 = get_target_service()
        service2 = get_target_service()
        assert service1 is service2


class TestFrontendBackendCompatibilitySync:
    """Guard against drift between frontend isCompatible() and backend TARGET_EVAL_PARAMS.

    The frontend pre-filters the Create RoundRobinTarget dropdown using a hardcoded
    set of fields (target_type + TARGET_EVAL_PARAMS). If the backend adds a new
    behavioral param, this test fails and reminds the developer to update
    frontend/src/components/Config/CreateTargetDialog.tsx → isCompatible().
    """

    def test_target_eval_params_match_frontend_iscompatible(self) -> None:
        """TARGET_EVAL_PARAMS must be exactly {underlying_model_name, temperature, top_p}.

        If this fails, someone added or removed a param from TARGET_EVAL_PARAMS.
        Update the frontend isCompatible() function in CreateTargetDialog.tsx
        to check the same fields, then update the expected set here.
        """
        from pyrit.models import TARGET_EVAL_PARAMS

        expected = {"underlying_model_name", "temperature", "top_p"}
        assert expected == TARGET_EVAL_PARAMS, (
            f"TARGET_EVAL_PARAMS changed to {TARGET_EVAL_PARAMS}. "
            f"Update the frontend isCompatible() in CreateTargetDialog.tsx to match, "
            f"then update this test's expected set."
        )

    def test_target_eval_param_fallbacks_match_frontend(self) -> None:
        """TARGET_EVAL_PARAM_FALLBACKS must match the fallback rule implemented in
        the frontend effectiveUnderlyingModel() helper in CreateTargetDialog.tsx.

        If this fails, someone added or changed a fallback. Update
        effectiveUnderlyingModel() (and any sibling resolvers) in
        CreateTargetDialog.tsx so the frontend pre-filter agrees with what the
        backend RoundRobinTarget._validate_behavioral_consistency check accepts,
        then update this test's expected dict.
        """
        from pyrit.models import TARGET_EVAL_PARAM_FALLBACKS

        expected = {"underlying_model_name": "model_name"}
        assert expected == TARGET_EVAL_PARAM_FALLBACKS, (
            f"TARGET_EVAL_PARAM_FALLBACKS changed to {TARGET_EVAL_PARAM_FALLBACKS}. "
            f"Update effectiveUnderlyingModel() in CreateTargetDialog.tsx to match, "
            f"then update this test's expected dict."
        )


# ============================================================================
# Capability Validation Tests
# ============================================================================


def _fake_target_with_capabilities(
    *,
    input_modalities: frozenset[frozenset[str]] | None = None,
    supports_json_schema: bool = True,
) -> MagicMock:
    """
    Build a MagicMock target whose ``capabilities`` attribute is a real
    ``TargetCapabilities`` object. The discovery engine is mocked separately,
    so we don't need a real PromptTarget subclass — just the ``.capabilities``
    attribute that ``validate_target_capabilities_async`` reads.
    """
    from pyrit.prompt_target.common.target_capabilities import TargetCapabilities

    caps = TargetCapabilities(
        supports_multi_turn=True,
        supports_multi_message_pieces=True,
        supports_json_schema=supports_json_schema,
        supports_json_output=True,
        supports_editable_history=True,
        supports_system_prompt=True,
        input_modalities=(input_modalities if input_modalities is not None else frozenset({frozenset(["text"])})),
    )
    target = MagicMock()
    target.capabilities = caps
    return target


def _fake_observed_capabilities(
    *,
    declared,  # TargetCapabilities
    drop_input_modalities: set[frozenset[str]] | None = None,
    flip_json_schema_to_false: bool = False,
):
    """
    Build a fake "observed" TargetCapabilities for the mock engine to return.

    Mirrors what the real engine produces: starts from ``declared`` and
    selectively drops/flips fields to simulate drift.
    """
    from pyrit.prompt_target.common.target_capabilities import TargetCapabilities

    observed_input = declared.input_modalities
    if drop_input_modalities:
        observed_input = frozenset(c for c in observed_input if c not in drop_input_modalities)
    return TargetCapabilities(
        supports_multi_turn=declared.supports_multi_turn,
        supports_multi_message_pieces=declared.supports_multi_message_pieces,
        supports_json_schema=False if flip_json_schema_to_false else declared.supports_json_schema,
        supports_json_output=declared.supports_json_output,
        supports_editable_history=declared.supports_editable_history,
        supports_system_prompt=declared.supports_system_prompt,
        input_modalities=observed_input,
        output_modalities=declared.output_modalities,
    )


class TestValidateTargetCapabilities:
    """Tests for TargetService.validate_target_capabilities_async."""

    async def test_returns_none_for_unknown_target(self) -> None:
        """Unknown registry name returns None; engine is NOT called."""
        from unittest.mock import AsyncMock

        service = TargetService()
        with patch(
            "pyrit.backend.services.target_service.discover_target_capabilities_async",
            new_callable=AsyncMock,
        ) as mock_probe:
            result = await service.validate_target_capabilities_async(target_registry_name="missing")
        assert result is None
        mock_probe.assert_not_called()

    async def test_returns_response_for_known_target(self) -> None:
        """Happy path: declared + observed populated, warnings present, no non-probeable."""
        from unittest.mock import AsyncMock

        service = TargetService()
        fake_target = _fake_target_with_capabilities()
        observed = _fake_observed_capabilities(declared=fake_target.capabilities)
        with (
            patch.object(service, "get_target_object", return_value=fake_target),
            patch(
                "pyrit.backend.services.target_service.discover_target_capabilities_async",
                new_callable=AsyncMock,
            ) as mock_probe,
        ):
            mock_probe.return_value = observed
            result = await service.validate_target_capabilities_async(target_registry_name="t1")

        assert result is not None
        assert result.target_registry_name == "t1"
        assert result.declared.supports_json_schema is True
        assert result.observed.supports_json_schema is True
        assert result.non_probeable_input_modalities == []
        # 5 base warnings, no 6th (no non-probeable)
        assert len(result.warnings) == 5

    async def test_passes_timeout_override(self) -> None:
        """Caller-supplied per_probe_timeout_s reaches the discovery call."""
        from unittest.mock import AsyncMock

        service = TargetService()
        fake_target = _fake_target_with_capabilities()
        observed = _fake_observed_capabilities(declared=fake_target.capabilities)
        with (
            patch.object(service, "get_target_object", return_value=fake_target),
            patch(
                "pyrit.backend.services.target_service.discover_target_capabilities_async",
                new_callable=AsyncMock,
            ) as mock_probe,
        ):
            mock_probe.return_value = observed
            await service.validate_target_capabilities_async(target_registry_name="t1", per_probe_timeout_s=10.0)
        assert mock_probe.call_args.kwargs["per_probe_timeout_s"] == 10.0

    async def test_uses_gui_default_timeout_when_not_overridden(self) -> None:
        """When per_probe_timeout_s is None, the GUI default (5.0) is passed."""
        from unittest.mock import AsyncMock

        service = TargetService()
        fake_target = _fake_target_with_capabilities()
        observed = _fake_observed_capabilities(declared=fake_target.capabilities)
        with (
            patch.object(service, "get_target_object", return_value=fake_target),
            patch(
                "pyrit.backend.services.target_service.discover_target_capabilities_async",
                new_callable=AsyncMock,
            ) as mock_probe,
        ):
            mock_probe.return_value = observed
            await service.validate_target_capabilities_async(target_registry_name="t1")
        assert mock_probe.call_args.kwargs["per_probe_timeout_s"] == TargetService._GUI_VALIDATE_TIMEOUT_S
        assert mock_probe.call_args.kwargs["per_probe_timeout_s"] == 15.0

    async def test_passes_probeable_modalities_only(self) -> None:
        """
        CRITICAL regression guard: only probeable modality combinations
        reach the engine. Non-probeable combos appear in the response's
        ``non_probeable_input_modalities`` list and in a warning.
        """
        from unittest.mock import AsyncMock

        service = TargetService()
        fake_target = _fake_target_with_capabilities(
            input_modalities=frozenset(
                {
                    frozenset(["text"]),
                    frozenset(["text", "image_path"]),
                    frozenset(["function_call"]),
                    frozenset(["url"]),
                }
            )
        )
        observed = _fake_observed_capabilities(declared=fake_target.capabilities)
        with (
            patch.object(service, "get_target_object", return_value=fake_target),
            patch(
                "pyrit.backend.services.target_service.discover_target_capabilities_async",
                new_callable=AsyncMock,
            ) as mock_probe,
        ):
            mock_probe.return_value = observed
            result = await service.validate_target_capabilities_async(target_registry_name="t1")

        # (a) only the two probeable combos reach the engine
        passed = mock_probe.call_args.kwargs["test_modalities"]
        assert passed == {frozenset(["text"]), frozenset(["text", "image_path"])}

        # (b) non-probeable types appear in the warnings list
        assert result is not None
        non_probed_warning = [w for w in result.warnings if "no packaged probe asset" in w]
        assert len(non_probed_warning) == 1
        assert "function_call" in non_probed_warning[0]
        assert "url" in non_probed_warning[0]

        # (c) typed field has the sorted, '+'-joined list
        assert result.non_probeable_input_modalities == ["function_call", "url"]

    async def test_passes_empty_set_when_no_probeable_modalities(self) -> None:
        """
        Declared modalities are all non-probeable. Method passes
        ``test_modalities=set()`` (NOT None) so the engine short-circuits
        cleanly without entering ``_permissive_configuration``. Warnings
        still include the not-probed entry, and the typed field lists every
        declared combo.
        """
        from unittest.mock import AsyncMock

        service = TargetService()
        fake_target = _fake_target_with_capabilities(
            input_modalities=frozenset(
                {
                    frozenset(["function_call"]),
                    frozenset(["url"]),
                    frozenset(["video_path"]),
                }
            )
        )
        observed = _fake_observed_capabilities(declared=fake_target.capabilities)
        with (
            patch.object(service, "get_target_object", return_value=fake_target),
            patch(
                "pyrit.backend.services.target_service.discover_target_capabilities_async",
                new_callable=AsyncMock,
            ) as mock_probe,
        ):
            mock_probe.return_value = observed
            result = await service.validate_target_capabilities_async(target_registry_name="t1")

        passed = mock_probe.call_args.kwargs["test_modalities"]
        assert passed == set()
        assert isinstance(passed, set)
        assert result is not None
        assert result.non_probeable_input_modalities == ["function_call", "url", "video_path"]
        assert any("no packaged probe asset" in w for w in result.warnings)

    async def test_propagates_probe_exceptions(self) -> None:
        """Engine raises → method raises. Lock is released even on exception."""
        from unittest.mock import AsyncMock

        service = TargetService()
        fake_target = _fake_target_with_capabilities()
        with (
            patch.object(service, "get_target_object", return_value=fake_target),
            patch(
                "pyrit.backend.services.target_service.discover_target_capabilities_async",
                new_callable=AsyncMock,
                side_effect=RuntimeError("engine boom"),
            ),
        ):
            with pytest.raises(RuntimeError, match="engine boom"):
                await service.validate_target_capabilities_async(target_registry_name="t1")

        # Lock must be released (the dict entry stays, but the lock isn't held).
        lock = service._validate_locks["t1"]
        assert not lock.locked(), "lock leaked after engine raised"

    async def test_serializes_concurrent_calls_on_same_target(self) -> None:
        """
        Two concurrent calls on the same registry name within the same service
        instance + same event loop serialize via the per-target lock.
        """
        import asyncio

        service = TargetService()
        fake_target = _fake_target_with_capabilities()
        observed = _fake_observed_capabilities(declared=fake_target.capabilities)

        first_running = asyncio.Event()
        release_first = asyncio.Event()
        order: list[str] = []

        async def slow_first(**_kwargs):
            order.append("first-enter")
            first_running.set()
            await release_first.wait()
            order.append("first-exit")
            return observed

        async def fast_second(**_kwargs):
            order.append("second-enter")
            order.append("second-exit")
            return observed

        call_count = {"n": 0}

        async def dispatch(**kwargs):
            call_count["n"] += 1
            return await (slow_first(**kwargs) if call_count["n"] == 1 else fast_second(**kwargs))

        with (
            patch.object(service, "get_target_object", return_value=fake_target),
            patch(
                "pyrit.backend.services.target_service.discover_target_capabilities_async",
                new=dispatch,
            ),
        ):
            task_first = asyncio.create_task(service.validate_target_capabilities_async(target_registry_name="t1"))
            await first_running.wait()
            task_second = asyncio.create_task(service.validate_target_capabilities_async(target_registry_name="t1"))
            # Give scheduler a tick — second must NOT have started.
            await asyncio.sleep(0.05)
            assert "second-enter" not in order, f"second leaked through: {order}"
            release_first.set()
            await asyncio.gather(task_first, task_second)

        assert order == ["first-enter", "first-exit", "second-enter", "second-exit"]

    async def test_allows_concurrent_calls_on_different_targets(self) -> None:
        """Two concurrent calls on different targets do NOT serialize."""
        import asyncio

        service = TargetService()
        fake_a = _fake_target_with_capabilities()
        fake_b = _fake_target_with_capabilities()
        observed_a = _fake_observed_capabilities(declared=fake_a.capabilities)
        observed_b = _fake_observed_capabilities(declared=fake_b.capabilities)

        a_running = asyncio.Event()
        b_started = asyncio.Event()

        async def dispatch_a(**_kwargs):
            a_running.set()
            await b_started.wait()  # must NOT block on B if locks are per-target
            return observed_a

        async def dispatch_b(**_kwargs):
            b_started.set()
            return observed_b

        def get_target(*, target_registry_name: str):
            return fake_a if target_registry_name == "a" else fake_b

        # Per-target dispatch via call_args inspection
        async def probe(*, target, **kwargs):
            if target is fake_a:
                return await dispatch_a(**kwargs)
            return await dispatch_b(**kwargs)

        with (
            patch.object(service, "get_target_object", side_effect=get_target),
            patch(
                "pyrit.backend.services.target_service.discover_target_capabilities_async",
                new=probe,
            ),
        ):
            task_a = asyncio.create_task(service.validate_target_capabilities_async(target_registry_name="a"))
            await a_running.wait()
            task_b = asyncio.create_task(service.validate_target_capabilities_async(target_registry_name="b"))
            # If locks were shared, task_a would deadlock waiting on b_started.
            result_a, result_b = await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=2.0)
        assert result_a is not None and result_b is not None

    async def test_creates_fresh_lock_per_service_instance(self) -> None:
        """
        Two TargetService() instances have independent _validate_locks dicts.
        Guards the R5 instance-attribute fix against accidental re-promotion
        to ClassVar (which would leak locks across pytest event loops).
        """
        from unittest.mock import AsyncMock

        service_a = TargetService()
        service_b = TargetService()
        fake_target_a = _fake_target_with_capabilities()
        fake_target_b = _fake_target_with_capabilities()
        observed_a = _fake_observed_capabilities(declared=fake_target_a.capabilities)
        observed_b = _fake_observed_capabilities(declared=fake_target_b.capabilities)

        # Trigger lock creation in both services for the same registry name
        with (
            patch.object(service_a, "get_target_object", return_value=fake_target_a),
            patch(
                "pyrit.backend.services.target_service.discover_target_capabilities_async",
                new_callable=AsyncMock,
                return_value=observed_a,
            ),
        ):
            await service_a.validate_target_capabilities_async(target_registry_name="shared")

        with (
            patch.object(service_b, "get_target_object", return_value=fake_target_b),
            patch(
                "pyrit.backend.services.target_service.discover_target_capabilities_async",
                new_callable=AsyncMock,
                return_value=observed_b,
            ),
        ):
            await service_b.validate_target_capabilities_async(target_registry_name="shared")

        assert "shared" in service_a._validate_locks
        assert "shared" in service_b._validate_locks
        # Different lock objects per service instance.
        assert service_a._validate_locks["shared"] is not service_b._validate_locks["shared"]

    async def test_includes_expected_warnings(self) -> None:
        """All five base warnings are present, in the documented order."""
        from unittest.mock import AsyncMock

        service = TargetService()
        fake_target = _fake_target_with_capabilities()
        observed = _fake_observed_capabilities(declared=fake_target.capabilities)
        with (
            patch.object(service, "get_target_object", return_value=fake_target),
            patch(
                "pyrit.backend.services.target_service.discover_target_capabilities_async",
                new_callable=AsyncMock,
                return_value=observed,
            ),
        ):
            result = await service.validate_target_capabilities_async(target_registry_name="t1")

        assert result is not None
        # Five base warnings (no 6th because no non-probeable modalities).
        assert len(result.warnings) == 5
        joined = " | ".join(result.warnings)
        assert "live requests" in joined  # cost/side-effects
        assert "capability_probe" in joined  # memory tagging
        assert "Output modalities are reported as declared" in joined
        assert "semantic enforcement" in joined  # request-vs-enforcement caveat
        assert "Do not run Validate while an attack" in joined  # validate-vs-attack
