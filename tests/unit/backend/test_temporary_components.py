# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Private construction, reconstruction, lifetime, and provenance regressions."""

import asyncio
import threading
import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from pyrit.backend.models.attacks import (
    CreateAttackRequest,
    MessagePieceRequest,
    MessageRequest,
    SaveConversationRequest,
)
from pyrit.backend.models.converters import ConverterPreviewRequest, CreateConverterRequest
from pyrit.backend.models.message_sends import MessageSendRequest, MessageSendState
from pyrit.backend.models.scorers import CreateScorerRequest
from pyrit.backend.models.targets import CreateTargetRequest
from pyrit.backend.routes import converters, scorers, targets
from pyrit.backend.services.attack_service import AttackService
from pyrit.backend.services.component_lifecycle import construct_component_async
from pyrit.backend.services.converter_service import ConverterService, get_converter_service
from pyrit.backend.services.manual_send_scheduler import ManualSendScheduler
from pyrit.backend.services.message_send_service import MessageSendService, resolve_applied_converter_identifiers
from pyrit.backend.services.scorer_service import ScorerService, get_scorer_service
from pyrit.backend.services.target_service import TargetService, get_target_service
from pyrit.common.apply_defaults import reset_default_values, set_default_value
from pyrit.common.brick_contract import forward_init_parameters
from pyrit.converter import CaesarConverter, Converter, ConverterResult, LLMGenericTextConverter, TranslationConverter
from pyrit.memory import SQLiteMemory
from pyrit.models import JSONValue, PromptDataType
from pyrit.models.component_spec import SourceInstanceSpec, TargetBinding
from pyrit.prompt_target import OpenAIChatTarget
from pyrit.prompt_target.common.utils import _get_rate_limit_lock
from pyrit.registry import ConverterRegistry, ScorerRegistry, TargetRegistry
from unit.backend.mocks import _settle_send_async


@pytest.fixture(autouse=True)
def isolated_registries() -> Iterator[None]:
    for registry in (TargetRegistry, ConverterRegistry, ScorerRegistry):
        registry.reset_registry_singleton()
    for factory in (get_target_service, get_converter_service, get_scorer_service):
        factory.cache_clear()
    yield
    for factory in (get_target_service, get_converter_service, get_scorer_service):
        factory.cache_clear()
    for registry in (TargetRegistry, ConverterRegistry, ScorerRegistry):
        registry.reset_registry_singleton()


def source_target() -> OpenAIChatTarget:
    return OpenAIChatTarget(
        endpoint="https://example.test/v1",
        model_name="test",
        api_key="test-only-key",
        temperature=0.2,
        extra_body_parameters={"example": "preserved"},
        max_requests_per_minute=30,
    )


class _MutableConverter(Converter):
    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(self, *, settings: dict[str, list[str]]) -> None:
        super().__init__()
        self.settings = settings
        self.settings["values"].append("constructed")

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        return ConverterResult(output_text=prompt, output_type=input_type)


class _ForwardedConverter(LLMGenericTextConverter):
    @forward_init_parameters
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)


@pytest.mark.usefixtures("patch_central_database")
class TestTemporaryComponents:
    @pytest.mark.parametrize(
        ("router", "path", "payload", "registry"),
        [
            (
                targets.router,
                "/api/targets",
                {
                    "type": "OpenAIChatTarget",
                    "register": False,
                    "params": {"endpoint": "https://example.test/v1", "model_name": "test", "api_key": "test-only-key"},
                },
                TargetRegistry,
            ),
            (
                converters.router,
                "/api/converters",
                {
                    "type": "CaesarConverter",
                    "register": False,
                    "params": {"caesar_offset": 3},
                },
                ConverterRegistry,
            ),
            (
                scorers.router,
                "/api/scorers",
                {
                    "type": "SubStringScorer",
                    "register": False,
                    "params": {"substring": "example"},
                },
                ScorerRegistry,
            ),
        ],
    )
    def test_rest_unregistered_build_has_no_handle(
        self,
        *,
        router: APIRouter,
        path: str,
        payload: dict[str, JSONValue],
        registry: type[TargetRegistry] | type[ConverterRegistry] | type[ScorerRegistry],
    ) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/api")
        with TestClient(app) as client:
            response = client.post(path, json=payload)
        assert response.status_code == 201, response.text
        assert "identifier" in response.json()
        assert not {"converter_id", "target_registry_name", "scorer_registry_name"} & response.json().keys()
        assert "test-only-key" not in response.text
        assert registry.get_registry_singleton().instances.get_names() == []

    async def test_temperature_reconstruction_is_private_and_preserves_auth_async(self) -> None:
        source = source_target()
        registry = TargetRegistry.get_registry_singleton()
        registry.instances.register(source, name="source")
        original = source.get_identifier()
        service = TargetService()
        spec = SourceInstanceSpec(source_name="source", source_hash=original.hash, params={"temperature": 1.5})
        built = await service.create_target_async(
            request=CreateTargetRequest(
                type="OpenAIChatTarget",
                register=False,
                source=spec,
            )
        )
        assert built.identifier.temperature == 1.5
        binding = TargetBinding(
            source_name="source",
            source_hash=original.hash,
            temperature=1.5,
            effective_hash=built.identifier.hash,
        )
        restored = await TargetService().resolve_binding_async(TargetBinding.from_metadata(binding.to_metadata()))
        try:
            assert restored is not source
            assert restored.get_identifier().hash == built.identifier.hash
            assert restored.get_reconstruction_parameters()["api_key"] == "test-only-key"
            assert restored._extra_body_parameters == {"example": "preserved"}
            assert restored._rate_limit_source is source
            assert _get_rate_limit_lock(restored) is _get_rate_limit_lock(source)
            assert not source._client.is_closed()
            assert source.get_identifier() is original
            assert source._temperature == 0.2
            assert registry.instances.get_names() == ["source"]
            assert "test-only-key" not in str(binding.to_metadata())
        finally:
            await restored.cleanup_target_async()
            await source.cleanup_target_async()

    async def test_two_temperature_variants_are_isolated_async(self) -> None:
        source = source_target()
        TargetRegistry.get_registry_singleton().instances.register(source, name="source")
        service = TargetService()
        first, second = await asyncio.gather(
            *[
                service.build_from_source_async(
                    SourceInstanceSpec(
                        source_name="source",
                        source_hash=source.get_identifier().hash,
                        params={"temperature": temperature},
                    )
                )
                for temperature in (0.8, 1.5)
            ]
        )
        try:
            assert first._temperature == 0.8
            assert second._temperature == 1.5
            assert source._temperature == 0.2
            assert first._client is not second._client
            first._extra_body_parameters["example"] = "private"
            assert second._extra_body_parameters == {"example": "preserved"}
            assert source._extra_body_parameters == {"example": "preserved"}
        finally:
            await first.cleanup_target_async()
            await second.cleanup_target_async()
            await source.cleanup_target_async()

    async def test_effective_hash_mismatch_releases_only_the_private_target_async(self) -> None:
        source = source_target()
        registry = TargetRegistry.get_registry_singleton()
        registry.instances.register(source, name="source")
        private = registry.recreate_instance(source=source, params={"temperature": 0.8})
        spec = SourceInstanceSpec(
            source_name="source",
            source_hash=source.get_identifier().hash,
            params={"temperature": 0.8},
            effective_hash="changed",
        )
        try:
            with patch.object(registry, "recreate_instance", return_value=private):
                with pytest.raises(ValueError, match="cannot be reconstructed"):
                    await TargetService().build_from_source_async(spec)
            assert private._client.is_closed()
            assert not source._client.is_closed()
        finally:
            await private.cleanup_target_async()
            await source.cleanup_target_async()

    def test_constructor_inputs_are_copied_and_custom_alias_is_resolved(self) -> None:
        source = _MutableConverter(settings={"values": ["source"]})
        registry = ConverterRegistry.get_registry_singleton()
        registry.register_class(_MutableConverter, name="custom")
        derived = registry.recreate_instance(source=source, params={})
        assert source.settings == {"values": ["source", "constructed"]}
        assert derived.settings == source.settings
        derived.settings["values"].append("derived")
        assert source.settings == {"values": ["source", "constructed"]}

    async def test_resolved_default_target_is_retained_async(self) -> None:
        source = source_target()
        replacement = source_target()
        try:
            set_default_value(class_type=LLMGenericTextConverter, parameter_name="converter_target", value=source)
            converter = LLMGenericTextConverter()
            set_default_value(class_type=LLMGenericTextConverter, parameter_name="converter_target", value=replacement)
            rebuilt = ConverterRegistry.get_registry_singleton().recreate_instance(source=converter, params={})
            assert rebuilt._converter_target is source
        finally:
            reset_default_values()
            await source.cleanup_target_async()
            await replacement.cleanup_target_async()

    async def test_translation_reconstruction_uses_only_its_declared_inputs_async(self) -> None:
        target = source_target()
        replacement = source_target()
        try:
            set_default_value(class_type=TranslationConverter, parameter_name="converter_target", value=target)
            source = TranslationConverter(language="Spanish", max_retries=7)
            identifier = source.get_identifier()
            set_default_value(class_type=TranslationConverter, parameter_name="converter_target", value=replacement)
            registry = ConverterRegistry.get_registry_singleton()
            inputs = registry.get_reconstruction_parameters(source)
            assert inputs["converter_target"] is target
            assert "system_prompt_template" not in inputs
            rebuilt = registry.recreate_instance(source=source, params={"language": "French", "max_retries": "4"})
            assert rebuilt.language == "french"
            assert rebuilt._prompt_kwargs["language"] == "french"
            assert rebuilt.converter_target is target
            assert rebuilt._max_retry_attempts == 4
            assert source.language == "spanish"
            assert source._max_retry_attempts == 7
            assert source.get_identifier() is identifier
        finally:
            reset_default_values()
            await target.cleanup_target_async()
            await replacement.cleanup_target_async()

    async def test_forwarded_constructor_reconstruction_retains_parent_defaults_async(self) -> None:
        target = source_target()
        replacement = source_target()
        try:
            set_default_value(class_type=_ForwardedConverter, parameter_name="converter_target", value=target)
            source = _ForwardedConverter()
            set_default_value(class_type=_ForwardedConverter, parameter_name="converter_target", value=replacement)
            registry = ConverterRegistry.get_registry_singleton()
            registry.register_class(_ForwardedConverter, name="forwarded")
            rebuilt = registry.recreate_instance(source=source, params={"max_retry_attempts": "5"})
            assert rebuilt._converter_target is target
            assert rebuilt._max_retry_attempts == 5
        finally:
            reset_default_values()
            await target.cleanup_target_async()
            await replacement.cleanup_target_async()

    async def test_opaque_constructor_inputs_are_not_silently_discarded_async(self) -> None:
        target = source_target()
        try:
            converter = LLMGenericTextConverter(converter_target=target, language="French")
            with pytest.raises(ValueError, match="undeclared constructor inputs: language"):
                ConverterRegistry.get_registry_singleton().recreate_instance(source=converter, params={})
        finally:
            await target.cleanup_target_async()

    async def test_target_settings_capability_rejects_undeclared_inputs_async(self) -> None:
        target = source_target()
        TargetRegistry.get_registry_singleton().instances.register(target, name="source")
        try:
            with patch.dict(target._reconstruction_parameters, {"opaque_setting": "preserve"}):
                descriptor = await TargetService().get_target_async(target_registry_name="source")
                assert descriptor is not None
                assert not descriptor.reconstructable
                assert not descriptor.supports_temperature_override
                assert descriptor.reconstruction_error == (
                    "The source uses undeclared constructor inputs: opaque_setting"
                )
        finally:
            await target.cleanup_target_async()

    async def test_cancelled_thread_build_releases_component_async(self) -> None:
        started = threading.Event()
        finish = threading.Event()
        component = source_target()
        cleanup = AsyncMock()

        def build() -> OpenAIChatTarget:
            started.set()
            assert finish.wait(timeout=5)
            return component

        with patch.object(component, "cleanup_target_async", cleanup):
            task = asyncio.create_task(construct_component_async(build))
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            cleanup.assert_awaited_once()
        await component.cleanup_target_async()

    async def test_attack_binding_persists_and_same_attack_is_immutable_async(
        self, sqlite_instance: SQLiteMemory
    ) -> None:
        source = source_target()
        TargetRegistry.get_registry_singleton().instances.register(source, name="source")
        built = await TargetService().build_from_source_async(
            SourceInstanceSpec(
                source_name="source",
                source_hash=source.get_identifier().hash,
                params={"temperature": 0.8},
            )
        )
        binding = TargetBinding(
            source_name="source",
            source_hash=source.get_identifier().hash,
            temperature=0.8,
            effective_hash=built.get_identifier().hash,
        )
        await built.cleanup_target_async()
        sender = MessageSendService(scheduler=ManualSendScheduler())
        service = AttackService(message_send_service=sender)
        try:
            created = await service.create_attack_async(
                request=CreateAttackRequest(
                    target_registry_name="source",
                    target_binding=binding,
                )
            )
            [stored] = await sqlite_instance.get_attack_results_async(attack_result_ids=[created.attack_result_id])
            assert TargetBinding.from_metadata(stored.metadata) == binding
            detail = await AttackService().get_attack_async(attack_result_id=created.attack_result_id)
            assert detail.target.binding == binding
            assert detail.target.identifier_hash == binding.effective_hash
            request = SaveConversationRequest(
                save_id=uuid.uuid4(),
                destination="same_attack",
                attack_result_id=created.attack_result_id,
                target_registry_name="source",
                target_binding=binding,
                messages=[MessageRequest(role="user", pieces=[MessagePieceRequest(original_value="test")])],
            )
            await service.save_conversation_async(request=request)
            request.target_binding = binding.model_copy(update={"temperature": 1.2})
            request.save_id = uuid.uuid4()
            with pytest.raises(ValueError, match="keep its temperature"):
                await service.save_conversation_async(request=request)
        finally:
            await sender.shutdown_async()
            await source.cleanup_target_async()

    async def test_reconstruction_rejects_wrong_type_unknown_override_and_effective_hash_async(self) -> None:
        source = CaesarConverter(caesar_offset=1)
        ConverterRegistry.get_registry_singleton().instances.register(source, name="source")
        spec = SourceInstanceSpec(
            source_name="source",
            source_hash=source.get_identifier().hash,
            params={"unknown": True},
        )
        service = ConverterService()
        try:
            with pytest.raises(ValueError, match="Unknown constructor parameters"):
                await service.create_converter_async(
                    request=CreateConverterRequest(
                        type="CaesarConverter",
                        source=spec,
                        register=False,
                    )
                )
            with pytest.raises(ValueError, match="type does not match"):
                await service.create_converter_async(
                    request=CreateConverterRequest(
                        type="Base64Converter",
                        source=spec,
                        register=False,
                    )
                )
            spec.params = {}
            spec.effective_hash = "different"
            with pytest.raises(ValueError, match="configuration has changed"):
                await service.create_converter_async(
                    request=CreateConverterRequest(
                        type="CaesarConverter",
                        source=spec,
                        register=False,
                    )
                )
        finally:
            await service.close_async()

    async def test_missing_changed_and_renamed_sources_async(self) -> None:
        source = source_target()
        registry = TargetRegistry.get_registry_singleton()
        registry.instances.register(source, name="source")
        spec = SourceInstanceSpec(
            source_name="source",
            source_hash=source.get_identifier().hash,
            params={"temperature": 0.8},
        )
        registry.instances.unregister("source")
        with pytest.raises(ValueError, match="missing or ambiguous"):
            await TargetService().build_from_source_async(spec)
        registry.instances.register(source, name="renamed")
        restored = await TargetService().build_from_source_async(spec)
        await restored.cleanup_target_async()
        changed = OpenAIChatTarget(
            endpoint="https://example.test/v1",
            model_name="different",
            api_key="test-only-key",
        )
        registry.instances.register(changed, name="source")
        with pytest.raises(ValueError, match="changed"):
            await TargetService().build_from_source_async(spec)
        await changed.cleanup_target_async()
        await source.cleanup_target_async()

    async def test_source_overrides_keep_external_input_validation_async(self) -> None:
        source = source_target()
        TargetRegistry.get_registry_singleton().instances.register(source, name="source")
        try:
            with pytest.raises(ValueError, match="cannot be set through the API"):
                await TargetService().build_from_source_async(
                    SourceInstanceSpec(
                        source_name="source",
                        source_hash=source.get_identifier().hash,
                        params={"httpx_client_kwargs": {"timeout": 5}},
                    )
                )
        finally:
            await source.cleanup_target_async()

    async def test_repeated_send_owns_each_temperature_target_async(self, sqlite_instance: SQLiteMemory) -> None:
        source = source_target()
        TargetRegistry.get_registry_singleton().instances.register(source, name="source")
        built = await TargetService().build_from_source_async(
            SourceInstanceSpec(
                source_name="source", source_hash=source.get_identifier().hash, params={"temperature": 0.8}
            )
        )
        binding = TargetBinding(
            source_name="source",
            source_hash=source.get_identifier().hash,
            temperature=0.8,
            effective_hash=built.get_identifier().hash,
        )
        await built.cleanup_target_async()
        sender = MessageSendService(scheduler=ManualSendScheduler())
        try:
            created = await AttackService(message_send_service=sender).create_attack_async(
                request=CreateAttackRequest(target_registry_name="source", target_binding=binding)
            )
            with patch.object(sender, "_execute_message_async", new_callable=AsyncMock) as execute:
                status = await sender.submit_async(
                    attack_result_id=created.attack_result_id,
                    request=MessageSendRequest(
                        target_registry_name="source",
                        target_conversation_id=created.conversation_id,
                        submission_id="temporary-repeat",
                        count=3,
                        role="user",
                        pieces=[MessagePieceRequest(original_value="test")],
                    ),
                )
                status = await _settle_send_async(service=sender, status=status)
            assert status.state == MessageSendState.COMPLETED
            targets = [call.kwargs["target"] for call in execute.await_args_list]
            assert len(targets) == 3
            assert len({id(target) for target in targets}) == 3
            assert all(target._temperature == 0.8 and target._client.is_closed() for target in targets)
            assert all(target is not source for target in targets)
            assert not source._client.is_closed()
        finally:
            await sender.shutdown_async()
            await source.cleanup_target_async()

    async def test_converter_preview_isolated_and_provenance_survives_discard_async(self) -> None:
        source = CaesarConverter(caesar_offset=1)
        registry = ConverterRegistry.get_registry_singleton()
        registry.instances.register(source, name="caesar")
        service = get_converter_service()
        spec = SourceInstanceSpec(
            source_name="caesar",
            source_hash=source.get_identifier().hash,
            params={"caesar_offset": 3},
        )
        built = await service.create_converter_async(
            request=CreateConverterRequest(
                type="CaesarConverter",
                register=False,
                source=spec,
            )
        )
        assert built.identifier.hash != source.get_identifier().hash
        preview = await service.preview_conversion_async(
            request=ConverterPreviewRequest(
                original_value="abc",
                converter_ids=["caesar", "caesar"],
                converter_specs=[spec, None],
            )
        )
        assert preview.steps[0].output_value == "def"
        assert preview.converted_value == "efg"
        assert (await source.convert_async(prompt="abc", input_type="text")).output_text == "bcd"
        token = preview.steps[0].provenance
        assert token is not None
        registry.instances.unregister("caesar")
        piece = MessagePieceRequest(
            original_value="abc",
            converted_value="def",
            applied_converter_ids=["caesar"],
            applied_converter_provenance=[token],
        )
        assert resolve_applied_converter_identifiers([piece])[0][0].hash == preview.steps[0].identifier.hash
        with pytest.raises(ValueError, match="invalid"):
            service.read_provenance(("0" if token[0] != "0" else "1") + token[1:])
        replacement = ConverterService()
        with pytest.raises(ValueError, match="earlier runtime"):
            replacement.read_provenance(token)
        await replacement.close_async()
        await service.close_async()

    async def test_scorer_build_and_registration_default_async(self) -> None:
        service = ScorerService()
        await service.create_scorer_async(
            request=CreateScorerRequest(
                type="SubStringScorer",
                params={"substring": "test"},
                register=False,
            )
        )
        assert ScorerRegistry.get_registry_singleton().instances.get_names() == []
        await service.create_scorer_async(
            request=CreateScorerRequest(
                name="named",
                type="SubStringScorer",
                params={"substring": "test"},
            )
        )
        assert ScorerRegistry.get_registry_singleton().instances.get_names() == ["named"]

    @pytest.mark.parametrize("temperature", [-0.1, 2.1, float("nan"), float("inf")])
    def test_binding_rejects_invalid_temperature(self, temperature: float) -> None:
        with pytest.raises(ValueError):
            TargetBinding(source_name="source", source_hash="hash", effective_hash="hash", temperature=temperature)
