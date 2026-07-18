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
from pyrit.prompt_target import PromptTarget, TargetCapabilities
from pyrit.registry import TargetRegistry
from unit.mocks import MockPromptTarget


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the TargetRegistry singleton before each test."""
    TargetRegistry.reset_registry_singleton()
    yield
    TargetRegistry.reset_registry_singleton()


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


def _mock_prompt_target(*, identifier: ComponentIdentifier | None = None) -> MagicMock:
    """Create a MagicMock PromptTarget with an identifier and default capabilities."""
    mock_target = MagicMock(spec=PromptTarget)
    mock_target.get_identifier.return_value = identifier if identifier is not None else _mock_target_identifier()
    mock_target.capabilities = TargetCapabilities()
    return mock_target


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
        mock_target = _mock_prompt_target(identifier=_mock_target_identifier(endpoint="http://test"))
        service._registry.instances.register(mock_target, name="target-1")

        result = await service.list_targets_async()

        assert len(result.items) == 1
        assert result.items[0].target_registry_name == "target-1"
        assert result.items[0].identifier.class_name == "MockTarget"
        assert result.pagination.has_more is False

    async def test_list_targets_paginates_with_limit(self) -> None:
        """Test that list_targets respects the limit parameter."""
        service = TargetService()

        for i in range(5):
            mock_target = _mock_prompt_target()
            service._registry.instances.register(mock_target, name=f"target-{i}")

        result = await service.list_targets_async(limit=3)

        assert len(result.items) == 3
        assert result.pagination.limit == 3
        assert result.pagination.has_more is True
        assert result.pagination.next_cursor == result.items[-1].target_registry_name

    async def test_list_targets_cursor_returns_next_page(self) -> None:
        """Test that list_targets cursor skips to the correct position."""
        service = TargetService()

        for i in range(5):
            mock_target = _mock_prompt_target()
            service._registry.instances.register(mock_target, name=f"target-{i}")

        first_page = await service.list_targets_async(limit=2)
        second_page = await service.list_targets_async(limit=2, cursor=first_page.pagination.next_cursor)

        assert len(second_page.items) == 2
        assert second_page.items[0].target_registry_name != first_page.items[0].target_registry_name
        assert second_page.pagination.has_more is True

    async def test_list_targets_last_page_has_no_more(self) -> None:
        """Test that the last page has has_more=False and no next_cursor."""
        service = TargetService()

        for i in range(3):
            mock_target = _mock_prompt_target()
            service._registry.instances.register(mock_target, name=f"target-{i}")

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

        mock_target = _mock_prompt_target()
        service._registry.instances.register(mock_target, name="target-1")

        result = await service.get_target_async(target_registry_name="target-1")

        assert result is not None
        assert result.target_registry_name == "target-1"
        assert result.identifier.class_name == "MockTarget"

    async def test_list_targets_includes_extra_params_in_target_specific(self) -> None:
        """Test that extra identifier params (reasoning_effort etc.) appear in target_specific_params."""
        service = TargetService()

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
        mock_target = _mock_prompt_target(identifier=identifier)
        service._registry.instances.register(mock_target, name="response-target")

        result = await service.list_targets_async()

        assert len(result.items) == 1
        target = result.items[0]
        assert target.identifier.temperature == 1.0
        assert target.target_specific_params is not None
        assert target.target_specific_params["reasoning_effort"] == "high"
        assert target.target_specific_params["reasoning_summary"] == "auto"
        assert target.target_specific_params["max_output_tokens"] == 4096

    async def test_get_target_includes_extra_params_in_target_specific(self) -> None:
        """Test that get_target returns target_specific_params with extra identifier params."""
        service = TargetService()

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
        mock_target = _mock_prompt_target(identifier=identifier)
        service._registry.instances.register(mock_target, name="chat-target")

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
        mock_target = MagicMock(spec=PromptTarget)
        service._registry.instances.register(mock_target, name="target-1")

        result = service.get_target_object(target_registry_name="target-1")

        assert result is mock_target


class TestListTargetCatalog:
    """Tests for TargetService.list_target_catalog_async method."""

    async def test_catalog_returns_known_target_types(self) -> None:
        """The catalog exposes constructible target classes from the registry."""
        service = TargetService()

        result = await service.list_target_catalog_async()

        target_types = [item.target_type for item in result.items]
        assert "OpenAIChatTarget" in target_types
        assert "AzureMLChatTarget" in target_types

    async def test_catalog_includes_declarative_auth_facts(self) -> None:
        """Catalog entries surface the per-class auth facts the frontend needs."""
        service = TargetService()

        result = await service.list_target_catalog_async()

        openai_entry = next(item for item in result.items if item.target_type == "OpenAIChatTarget")
        assert "api_key" in openai_entry.supported_auth_modes
        assert "identity" in openai_entry.supported_auth_modes

    @pytest.mark.parametrize(
        ("target_type", "parameter_name", "type_name", "required", "choices"),
        [
            (
                "GandalfTarget",
                "level",
                "GandalfLevel",
                True,
                [
                    "baseline",
                    "do-not-tell",
                    "do-not-tell-and-block",
                    "gpt-is-password-encoded",
                    "word-blacklist",
                    "gpt-blacklist",
                    "gandalf",
                    "gandalf-the-white",
                    "adventure-1",
                    "adventure-2",
                ],
            ),
            (
                "AzureBlobStorageTarget",
                "blob_content_type",
                "SupportedContentType",
                False,
                ["text/plain", "text/html"],
            ),
            (
                "PlaywrightCopilotTarget",
                "copilot_type",
                "CopilotType",
                False,
                ["consumer", "m365"],
            ),
        ],
    )
    async def test_catalog_includes_enum_parameters(
        self,
        target_type: str,
        parameter_name: str,
        type_name: str,
        required: bool,
        choices: list[str],
    ) -> None:
        """Enum parameters are exposed with their required state and allowed values."""
        service = TargetService()

        result = await service.list_target_catalog_async()

        entry = next(item for item in result.items if item.target_type == target_type)
        parameter = next(param for param in entry.parameters if param.name == parameter_name)
        assert parameter.required is required
        assert parameter.type_name == type_name
        assert parameter.choices == choices


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
        assert result.identifier.class_name == "TextTarget"

    async def test_create_target_delegates_construction_to_registry(self, sqlite_instance) -> None:
        """Every target construction path is owned by the registry."""
        service = TargetService()
        with patch.object(service._registry, "create_instance", wraps=service._registry.create_instance) as create:
            await service.create_target_async(request=CreateTargetRequest(type="TextTarget", params={}))

        create.assert_called_once()

    async def test_create_gandalf_target_coerces_level_string(self, sqlite_instance) -> None:
        """A Gandalf level from the JSON request is coerced to its enum before construction."""
        service = TargetService()
        request = CreateTargetRequest(
            type="GandalfTarget",
            params={"level": "baseline"},
        )

        result = await service.create_target_async(request=request)

        assert result.identifier.class_name == "GandalfTarget"
        assert result.target_specific_params == {"level": "baseline"}

    async def test_create_gandalf_target_rejects_invalid_level(self, sqlite_instance) -> None:
        """An invalid Gandalf level raises a parameter error before target construction."""
        service = TargetService()
        request = CreateTargetRequest(
            type="GandalfTarget",
            params={"level": "unknown"},
        )

        with pytest.raises(ValueError, match="Parameter 'level'.*expected one of"):
            await service.create_target_async(request=request)

    async def test_create_azure_blob_target_coerces_content_type_string(self, sqlite_instance) -> None:
        """An explicit blob content type from JSON is coerced before target construction."""
        service = TargetService()
        request = CreateTargetRequest(
            type="AzureBlobStorageTarget",
            params={
                "container_url": "https://test.blob.core.windows.net/test",
                "sas_token": "valid_sas_token",
                "blob_content_type": "text/html",
            },
        )

        result = await service.create_target_async(request=request)

        target = service.get_target_object(target_registry_name=result.target_registry_name)
        assert target._blob_content_type == "text/html"

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

            assert result.identifier.model_name == "claude-sonnet-4-6"
            # underlying_model_name is empty since no underlying_model was passed
            assert not result.identifier.underlying_model_name

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

        assert result.identifier.model_name == "my-gpt4o-deployment"
        assert result.identifier.underlying_model_name == "gpt-4o"


class TestCreateTargetEntraAuth:
    """Entra auth at the service boundary: the service only omits the api_key and
    confirms the target type supports Entra. Endpoint trust + token minting are the
    target's job and are covered in the target-level tests (see
    tests/unit/prompt_target/target/)."""

    async def test_create_openai_target_with_entra_omits_key_and_target_mints_token(self, sqlite_instance) -> None:
        """Entra path: the service omits the api_key so the target mints its own token."""

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_CHAT_KEY", None)
            with patch(
                "pyrit.prompt_target.openai.openai_target.get_azure_openai_auth",
                return_value=_test_token_provider,
            ) as mock_get_auth:
                service = TargetService()

                request = CreateTargetRequest(
                    type="OpenAIChatTarget",
                    params={
                        "endpoint": "https://test.openai.azure.com/",
                        "model_name": "gpt-4o",
                    },
                    auth_mode="identity",
                )

                result = await service.create_target_async(request=request)

                mock_get_auth.assert_called_once_with("https://test.openai.azure.com/")
                target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
                assert target_obj is not None
                # OpenAI target preserves async callables verbatim through ensure_async_token_provider.
                assert target_obj._api_key is _test_token_provider  # type: ignore[attr-defined]

    async def test_create_openai_target_with_identity_drops_user_api_key(self, sqlite_instance) -> None:
        """Any api_key supplied alongside auth_mode='identity' must be discarded."""

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_CHAT_KEY", None)
            with patch(
                "pyrit.prompt_target.openai.openai_target.get_azure_openai_auth",
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
                    auth_mode="identity",
                )

                result = await service.create_target_async(request=request)

                target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
                assert target_obj is not None
                assert target_obj._api_key is _test_token_provider  # type: ignore[attr-defined]
                # The literal "should-be-ignored" string must never appear.
                assert target_obj._api_key != "should-be-ignored"  # type: ignore[attr-defined]

    async def test_create_openai_target_with_identity_does_not_mutate_request_params(self, sqlite_instance) -> None:
        """The CreateTargetRequest.params object must remain unchanged after creation."""

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_CHAT_KEY", None)
            with patch(
                "pyrit.prompt_target.openai.openai_target.get_azure_openai_auth",
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
                    auth_mode="identity",
                )

                await service.create_target_async(request=request)

                # The caller's request.params must be unchanged after the call.
                assert request.params == original_params

    async def test_create_azureml_target_with_identity_omits_key_and_target_mints_token(self, sqlite_instance) -> None:
        """AzureML identity path: the service omits the key so the target mints the ML scope token."""

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AZURE_ML_KEY", None)
            with patch(
                "pyrit.prompt_target.azure_ml_chat_target.get_azure_async_token_provider",
                return_value=_test_token_provider,
            ) as mock_get_provider:
                service = TargetService()

                request = CreateTargetRequest(
                    type="AzureMLChatTarget",
                    params={"endpoint": "https://my-aml.region.inference.ml.azure.com/score"},
                    auth_mode="identity",
                )

                result = await service.create_target_async(request=request)

                mock_get_provider.assert_called_once_with("https://ml.azure.com/.default")
                target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
                assert target_obj is not None
                # AzureMLChatTarget stores the provider on _api_key_provider; static _api_key is cleared.
                assert target_obj._api_key_provider is _test_token_provider  # type: ignore[attr-defined]
                assert target_obj._api_key == ""  # type: ignore[attr-defined]

    async def test_create_openai_target_with_identity_non_azure_endpoint_raises(self, sqlite_instance) -> None:
        """The target (not the service) rejects an unrecognized endpoint under identity auth."""

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_CHAT_KEY", None)
            service = TargetService()

            request = CreateTargetRequest(
                type="OpenAIChatTarget",
                params={"endpoint": "https://api.openai.com/", "model_name": "gpt-4o"},
                auth_mode="identity",
            )

            with pytest.raises(ValueError, match="non-Azure endpoints"):
                await service.create_target_async(request=request)

    async def test_create_target_identity_unsupported_type_raises(self, sqlite_instance) -> None:
        """Identity-based auth is only supported for targets that declare it."""
        service = TargetService()

        request = CreateTargetRequest(
            type="TextTarget",
            params={},
            auth_mode="identity",
        )

        with pytest.raises(ValueError, match="does not support identity-based authentication"):
            await service.create_target_async(request=request)


class TestCreateRoundRobinTarget:
    """Service-level tests for building RoundRobinTarget through the registry.

    The service passes ``targets`` (registry names) to ``registry.create_instance``;
    the resolver turns the names into live target objects and RoundRobinTarget owns
    its own construction validation (dedup, class/config consistency). Those rules
    are covered in tests/unit/prompt_target/test_round_robin_target.py — here we only
    exercise the service wiring.
    """

    async def test_create_round_robin_target_resolves_registry_names(self, sqlite_instance) -> None:
        """RoundRobinTarget creation resolves registry names to live target objects."""
        service = TargetService()

        target_a = MockPromptTarget()
        target_b = MockPromptTarget()
        service._registry.instances.register(target_a, name="target-a")
        service._registry.instances.register(target_b, name="target-b")

        rr_request = CreateTargetRequest(
            type="RoundRobinTarget",
            params={"targets": ["target-a", "target-b"], "weights": [2, 1]},
        )

        result = await service.create_target_async(request=rr_request)

        assert result.identifier.class_name == "RoundRobinTarget"
        target_obj = service.get_target_object(target_registry_name=result.target_registry_name)
        assert target_obj._targets == [target_a, target_b]
        assert target_obj._weights == [2, 1]

    async def test_create_round_robin_target_fewer_than_2_raises(self, sqlite_instance) -> None:
        """A single inner target bubbles up RoundRobinTarget's own validation error."""
        service = TargetService()

        service._registry.instances.register(MockPromptTarget(), name="only-one")

        rr_request = CreateTargetRequest(
            type="RoundRobinTarget",
            params={"targets": ["only-one"]},
        )

        with pytest.raises(ValueError, match="at least 2 targets"):
            await service.create_target_async(request=rr_request)

    async def test_create_round_robin_target_unknown_name_raises(self, sqlite_instance) -> None:
        """A non-existent registry name is rejected by the resolver."""
        service = TargetService()

        rr_request = CreateTargetRequest(
            type="RoundRobinTarget",
            params={"targets": ["does-not-exist-a", "does-not-exist-b"]},
        )

        with pytest.raises(ValueError, match="not found"):
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
