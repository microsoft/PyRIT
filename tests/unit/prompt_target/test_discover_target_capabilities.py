# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.models import Message, MessagePiece, PromptDataType
from pyrit.prompt_target.common.discover_target_capabilities import (
    _CAPABILITY_PROBES,
    DEFAULT_TEST_ASSETS,
    _create_test_message,
    _discover_capability_flags_async,
    _discover_input_modalities_async,
    _permissive_configuration,
    discover_target_capabilities_async,
)
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import (
    CapabilityHandlingPolicy,
    CapabilityName,
    TargetCapabilities,
    UnsupportedCapabilityBehavior,
)
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from tests.unit.mocks import MockPromptTarget


class _RealValidationTarget(PromptTarget):
    """
    Bare ``PromptTarget`` subclass that does NOT override ``_validate_request``.

    Tests that need to verify ``_permissive_configuration`` actually bypasses
    the validation guard use this instead of ``MockPromptTarget`` (which
    no-ops ``_validate_request``).
    """

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(),
    )

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        return _ok_response()


def _ok_response(*, conversation_id: str = "probe", text: str = "ok") -> list[Message]:
    return [
        Message(
            [
                MessagePiece(
                    role="assistant",
                    original_value=text,
                    original_value_data_type="text",
                    conversation_id=conversation_id,
                    response_error="none",
                )
            ]
        )
    ]


def _error_response(*, conversation_id: str = "probe") -> list[Message]:
    return [
        Message(
            [
                MessagePiece(
                    role="assistant",
                    original_value="blocked",
                    original_value_data_type="text",
                    conversation_id=conversation_id,
                    response_error="blocked",
                )
            ]
        )
    ]


@pytest.mark.usefixtures("patch_central_database")
class TestPermissiveConfiguration:
    def test_replaces_and_restores_configuration(self) -> None:
        target = MockPromptTarget()
        original = target.configuration

        with _permissive_configuration(target=target):
            permissive = target.configuration
            assert permissive is not original
            for capability in CapabilityName:
                assert permissive.includes(capability=capability)

        assert target.configuration is original

    def test_restores_on_exception(self) -> None:
        target = MockPromptTarget()
        original = target.configuration

        with pytest.raises(RuntimeError):
            with _permissive_configuration(target=target):
                raise RuntimeError("boom")

        assert target.configuration is original


@pytest.mark.usefixtures("patch_central_database")
class TestDiscoverTargetCapabilitiesAsync:
    async def test_returns_only_supported_when_all_probes_succeed(self) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(target=target)

        # Every capability with a probe should be in the result.
        for capability in _CAPABILITY_PROBES:
            assert capability in result

    async def test_excludes_capabilities_when_probe_fails(self) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(side_effect=Exception("nope"))  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(target=target)

        for capability in _CAPABILITY_PROBES:
            assert capability not in result

    async def test_excludes_capabilities_when_response_has_error(self) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=_error_response())  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(target=target)

        for capability in _CAPABILITY_PROBES:
            assert capability not in result

    async def test_filters_by_requested_capabilities(self) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        requested = {CapabilityName.SYSTEM_PROMPT, CapabilityName.MULTI_TURN}
        result = await _discover_capability_flags_async(target=target, capabilities=requested)

        assert result == requested

    async def test_capability_without_probe_falls_back_to_declared_support(self) -> None:
        target = MockPromptTarget()
        # Override the configuration so editable_history is declared as supported.
        target._configuration = TargetConfiguration(
            capabilities=TargetCapabilities(supports_editable_history=True),
        )
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.EDITABLE_HISTORY},
        )

        assert result == {CapabilityName.EDITABLE_HISTORY}

    async def test_capability_without_probe_excluded_when_not_declared(self) -> None:
        target = MockPromptTarget()
        # Override to a configuration that does NOT declare editable_history.
        target._configuration = TargetConfiguration(capabilities=TargetCapabilities())
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.EDITABLE_HISTORY},
        )

        assert result == set()

    async def test_capability_without_probe_excluded_when_only_adapted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """
        ADAPT in the policy must NOT count as native support for the fallback.

        Today every adaptable capability also has a probe, so this scenario only
        arises if a future capability is declared adaptable without a probe.
        We simulate that by removing SYSTEM_PROMPT from the registry and
        configuring the target with ``ADAPT`` for it but no native support.
        """
        from pyrit.prompt_target.common import discover_target_capabilities as qtc
        from pyrit.prompt_target.common.target_capabilities import (
            CapabilityHandlingPolicy,
            UnsupportedCapabilityBehavior,
        )

        patched_probes = {k: v for k, v in qtc._CAPABILITY_PROBES.items() if k is not CapabilityName.SYSTEM_PROMPT}
        monkeypatch.setattr(qtc, "_CAPABILITY_PROBES", patched_probes)

        target = MockPromptTarget()
        target._configuration = TargetConfiguration(
            capabilities=TargetCapabilities(),  # no native SYSTEM_PROMPT
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                }
            ),
        )

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.SYSTEM_PROMPT},
        )

        assert result == set()

    async def test_accepts_single_pass_iterable(self) -> None:
        """Passing a generator must not silently drop fallback (non-probed) capabilities."""
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(
            capabilities=TargetCapabilities(supports_editable_history=True),
        )
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        gen = (c for c in [CapabilityName.SYSTEM_PROMPT, CapabilityName.EDITABLE_HISTORY])
        result = await _discover_capability_flags_async(target=target, capabilities=gen)

        assert CapabilityName.SYSTEM_PROMPT in result
        assert CapabilityName.EDITABLE_HISTORY in result

    async def test_retries_zero_disables_retry(self) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(side_effect=Exception("boom"))  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.JSON_OUTPUT},
            retries=0,
        )

        assert result == set()
        assert target._send_prompt_to_target_async.await_count == 1

    async def test_retries_use_exponential_backoff(self) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(side_effect=Exception("boom"))  # type: ignore[method-assign]

        with patch(
            "pyrit.prompt_target.common.discover_target_capabilities.asyncio.sleep", new_callable=AsyncMock
        ) as sleep_mock:
            result = await _discover_capability_flags_async(
                target=target,
                capabilities={CapabilityName.JSON_OUTPUT},
                retries=2,
            )

        assert result == set()
        assert sleep_mock.await_args_list[0].args == (0.1,)
        assert sleep_mock.await_args_list[1].args == (0.2,)

    async def test_non_retryable_validation_errors_fail_fast(self) -> None:
        """
        Deterministic errors (ValueError/TypeError/AttributeError) come from
        malformed payloads or programmer error and will not become valid on
        a retry. They must fail the probe immediately without consuming the
        retry budget or sleeping for backoff.
        """
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(  # type: ignore[method-assign]
            side_effect=ValueError("malformed payload")
        )

        with patch(
            "pyrit.prompt_target.common.discover_target_capabilities.asyncio.sleep", new_callable=AsyncMock
        ) as sleep_mock:
            result = await _discover_capability_flags_async(
                target=target,
                capabilities={CapabilityName.JSON_OUTPUT},
                retries=3,
            )

        assert result == set()
        # No retries consumed and no backoff sleeps issued.
        assert target._send_prompt_to_target_async.await_count == 1
        sleep_mock.assert_not_awaited()

    async def test_restores_configuration_after_probing(self) -> None:
        target = MockPromptTarget()
        original = target.configuration
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        await _discover_capability_flags_async(target=target)

        assert target.configuration is original

    async def test_multi_turn_probe_sends_history_on_second_call(self) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.MULTI_TURN},
        )

        # Multi-turn probe sends two requests on the same conversation_id, and
        # seeds memory between them so the second call carries real history.
        calls = target._send_prompt_to_target_async.await_args_list
        assert len(calls) == 2

        first_conv = calls[0].kwargs["normalized_conversation"]
        second_conv = calls[1].kwargs["normalized_conversation"]

        first_conv_id = first_conv[-1].message_pieces[0].conversation_id
        second_conv_id = second_conv[-1].message_pieces[0].conversation_id
        assert first_conv_id == second_conv_id

        # First call is a single-turn user message; the second call must include
        # the seeded user + assistant history followed by the new user turn.
        assert len(first_conv) == 1
        assert len(second_conv) >= 3
        roles = [msg.message_pieces[0]._role for msg in second_conv]
        assert roles[-3:] == ["user", "assistant", "user"]

    async def test_multi_turn_probe_short_circuits_on_first_failure(self) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(side_effect=Exception("first call fails"))  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.MULTI_TURN},
        )

        assert result == set()
        # _send_and_check_async retries once on exception, so the failing
        # first turn is attempted twice; the second turn is never reached.
        assert target._send_prompt_to_target_async.await_count == 2

    async def test_json_schema_probe_sends_schema_in_metadata(self) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.JSON_SCHEMA},
        )

        normalized: list[Message] = target._send_prompt_to_target_async.await_args.kwargs["normalized_conversation"]
        metadata = normalized[-1].message_pieces[0].prompt_metadata
        assert metadata is not None
        assert metadata["response_format"] == "json"
        # Schema is JSON-encoded into a string for prompt_metadata's value type.
        schema = json.loads(metadata["json_schema"])
        assert schema["type"] == "object"

    @pytest.mark.parametrize("capability", [CapabilityName.JSON_OUTPUT, CapabilityName.JSON_SCHEMA])
    async def test_logs_debug_for_unenforced_json_probe(
        self, capability: CapabilityName, caplog: pytest.LogCaptureFixture
    ) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        with caplog.at_level(logging.DEBUG):
            result = await _discover_capability_flags_async(target=target, capabilities={capability})

        assert result == {capability}
        matching = [r for r in caplog.records if r.message.startswith("JSON capability probes")]
        assert len(matching) == 1
        assert capability.value in matching[0].message

    async def test_logs_unenforced_json_probe_summary_once_for_both_capabilities(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        with caplog.at_level(logging.DEBUG):
            await _discover_capability_flags_async(
                target=target,
                capabilities={CapabilityName.JSON_OUTPUT, CapabilityName.JSON_SCHEMA},
            )

        # A single summary line covers both probed JSON capabilities.
        matching = [r for r in caplog.records if r.message.startswith("JSON capability probes")]
        assert len(matching) == 1
        assert CapabilityName.JSON_OUTPUT.value in matching[0].message
        assert CapabilityName.JSON_SCHEMA.value in matching[0].message

    async def test_does_not_log_unenforced_json_probe_when_probe_fails(self, caplog: pytest.LogCaptureFixture) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(side_effect=Exception("boom"))  # type: ignore[method-assign]

        with caplog.at_level(logging.DEBUG):
            result = await _discover_capability_flags_async(
                target=target,
                capabilities={CapabilityName.JSON_OUTPUT, CapabilityName.JSON_SCHEMA},
                retries=0,
            )

        assert result == set()
        assert not any(r.message.startswith("JSON capability probes") for r in caplog.records)

    async def test_does_not_log_debug_for_enforced_json_probe(self, caplog: pytest.LogCaptureFixture) -> None:
        target_type = type("FakeEnforcingTarget", (MockPromptTarget,), {})
        target = target_type()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        with (
            patch(
                "pyrit.prompt_target.common.discover_target_capabilities._json_enforcing_target_types",
                return_value=(target_type,),
            ),
            caplog.at_level(logging.DEBUG),
        ):
            result = await _discover_capability_flags_async(
                target=target,
                capabilities={CapabilityName.JSON_OUTPUT},
            )

        assert result == {CapabilityName.JSON_OUTPUT}
        assert not any(r.message.startswith("JSON capability probes") for r in caplog.records)

    async def test_subclass_of_enforced_target_does_not_log(self, caplog: pytest.LogCaptureFixture) -> None:
        # ``isinstance`` covers user-defined subclasses of enforcing targets.
        base = type("EnforcingBase", (MockPromptTarget,), {})
        sub = type("UserSubclass", (base,), {})
        target = sub()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        with (
            patch(
                "pyrit.prompt_target.common.discover_target_capabilities._json_enforcing_target_types",
                return_value=(base,),
            ),
            caplog.at_level(logging.DEBUG),
        ):
            await _discover_capability_flags_async(
                target=target,
                capabilities={CapabilityName.JSON_OUTPUT},
            )

        assert not any(r.message.startswith("JSON capability probes") for r in caplog.records)

    async def test_system_prompt_probe_installs_system_message_and_sends_user(self) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.SYSTEM_PROMPT},
        )

        # The probe writes a system message directly to memory (bypassing
        # PromptTarget.set_system_prompt, which subclasses can override) and
        # then sends a user-role message. Message.validate forbids mixed
        # roles in a single Message, so the system and user turns are
        # separate. Verify the system message is in memory and the wire
        # payload contains the system + user history.
        normalized: list[Message] = target._send_prompt_to_target_async.await_args.kwargs["normalized_conversation"]
        roles_sent = [piece._role for msg in normalized for piece in msg.message_pieces]
        assert "system" in roles_sent
        assert roles_sent[-1] == "user"
        # The last sent Message itself should be user-only.
        assert [piece._role for piece in normalized[-1].message_pieces] == ["user"]

    async def test_multi_message_pieces_probe_sends_two_pieces(self) -> None:
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.MULTI_MESSAGE_PIECES},
        )

        normalized: list[Message] = target._send_prompt_to_target_async.await_args.kwargs["normalized_conversation"]
        assert len(normalized[-1].message_pieces) == 2

    async def test_probes_run_under_permissive_configuration(self) -> None:
        """
        Even when the target declares no boolean capabilities, the probe should
        still execute because the configuration is temporarily permissive.

        Uses ``_RealValidationTarget`` so that ``_validate_request`` actually
        runs and would reject the multi-piece probe were the override absent.
        """
        target = _RealValidationTarget()
        send_mock = AsyncMock(return_value=_ok_response())
        target._send_prompt_to_target_async = send_mock  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.MULTI_MESSAGE_PIECES},
        )

        # Probe was actually invoked through the full send_prompt_async pipeline,
        # which means _validate_request ran and was satisfied by the permissive
        # override (the bare target declares no capabilities natively).
        assert send_mock.await_count >= 1
        assert CapabilityName.MULTI_MESSAGE_PIECES in result

    async def test_probed_capability_excluded_when_only_adapted(self) -> None:
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(
            capabilities=TargetCapabilities(supports_system_prompt=False),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                }
            ),
        )

        async def reject_system_roles(*, normalized_conversation: list[Message]) -> list[Message]:
            roles = [piece._role for message in normalized_conversation for piece in message.message_pieces]
            if "system" in roles:
                raise RuntimeError("system messages are not natively supported")
            return _ok_response()

        target._send_prompt_to_target_async = AsyncMock(side_effect=reject_system_roles)  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.SYSTEM_PROMPT},
        )

        assert result == set()

    async def test_probe_configuration_does_not_reuse_adapted_pipeline(self) -> None:
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(
            capabilities=TargetCapabilities(supports_system_prompt=False),
            policy=CapabilityHandlingPolicy(
                behaviors={
                    CapabilityName.SYSTEM_PROMPT: UnsupportedCapabilityBehavior.ADAPT,
                    CapabilityName.MULTI_TURN: UnsupportedCapabilityBehavior.RAISE,
                }
            ),
        )

        async def require_native_system_role(*, normalized_conversation: list[Message]) -> list[Message]:
            roles = [piece._role for message in normalized_conversation for piece in message.message_pieces]
            if "system" not in roles:
                raise RuntimeError("probe used adapted system-prompt shaping")
            return _ok_response()

        target._send_prompt_to_target_async = AsyncMock(side_effect=require_native_system_role)  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.SYSTEM_PROMPT},
        )

        assert result == {CapabilityName.SYSTEM_PROMPT}


@pytest.mark.usefixtures("patch_central_database")
class TestDiscoverTargetCapabilitiesIsolatedTarget:
    """Tests using a bare PromptTarget subclass (no PromptChatTarget extras)."""

    async def test_with_minimal_target_subclass(self) -> None:
        class _MinimalTarget(PromptTarget):
            async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
                return _ok_response()

        target = _MinimalTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(target=target)

        for capability in _CAPABILITY_PROBES:
            assert capability in result


# ---------------------------------------------------------------------------
# Modality query tests
# ---------------------------------------------------------------------------


def _set_input_modalities(
    *,
    target: MockPromptTarget,
    modalities: set[frozenset[PromptDataType]],
) -> None:
    target._configuration = TargetConfiguration(
        capabilities=TargetCapabilities(
            input_modalities=frozenset(modalities),
        ),
    )


@pytest.fixture
def image_asset(tmp_path: Path) -> str:
    """Create a tiny placeholder file usable as an image_path asset."""
    asset = tmp_path / "test_image.png"
    asset.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(asset)


@pytest.mark.usefixtures("patch_central_database")
class TestCreateTestMessage:
    def test_default_assets_exist_for_packaged_modalities(self) -> None:
        msg = _create_test_message(
            modalities=frozenset({"audio_path", "image_path"}),
            test_assets=DEFAULT_TEST_ASSETS,
        )

        types = {piece.original_value_data_type for piece in msg.message_pieces}
        assert types == {"audio_path", "image_path"}

    def test_text_only(self) -> None:
        msg = _create_test_message(modalities=frozenset({"text"}), test_assets={})
        assert len(msg.message_pieces) == 1
        assert msg.message_pieces[0].original_value_data_type == "text"

    def test_multimodal_uses_assets(self, image_asset: str) -> None:
        msg = _create_test_message(
            modalities=frozenset({"text", "image_path"}),
            test_assets={"image_path": image_asset},
        )
        types = {piece.original_value_data_type for piece in msg.message_pieces}
        assert types == {"text", "image_path"}

        # All pieces share the same conversation_id (Message.validate requires it).
        conv_ids = {piece.conversation_id for piece in msg.message_pieces}
        assert len(conv_ids) == 1

    def test_missing_asset_file_raises_filenotfound(self, tmp_path: Path) -> None:
        missing_path = str(tmp_path / "does_not_exist.png")
        with pytest.raises(FileNotFoundError):
            _create_test_message(
                modalities=frozenset({"image_path"}),
                test_assets={"image_path": missing_path},
            )

    def test_unconfigured_modality_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="No test asset configured"):
            _create_test_message(
                modalities=frozenset({"image_path"}),
                test_assets={},
            )


@pytest.mark.usefixtures("patch_central_database")
class TestVerifyTargetModalitiesAsync:
    async def test_all_combinations_supported(self) -> None:
        target = MockPromptTarget()
        _set_input_modalities(target=target, modalities={frozenset({"text"})})
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        result = await _discover_input_modalities_async(target=target)

        assert frozenset({"text"}) in result

    async def test_exception_excludes_combination(self) -> None:
        target = MockPromptTarget()
        _set_input_modalities(target=target, modalities={frozenset({"text"})})
        target._send_prompt_to_target_async = AsyncMock(side_effect=Exception("nope"))  # type: ignore[method-assign]

        result = await _discover_input_modalities_async(target=target)

        assert result == set()

    async def test_error_response_excludes_combination(self) -> None:
        target = MockPromptTarget()
        _set_input_modalities(target=target, modalities={frozenset({"text"})})
        target._send_prompt_to_target_async = AsyncMock(return_value=_error_response())  # type: ignore[method-assign]

        result = await _discover_input_modalities_async(target=target)

        assert result == set()

    async def test_partial_support_via_selective_failure(self, image_asset: str) -> None:
        target = MockPromptTarget()
        _set_input_modalities(
            target=target,
            modalities={frozenset({"text"}), frozenset({"text", "image_path"})},
        )

        async def selective_send(*, normalized_conversation: list[Message]) -> list[Message]:
            message = normalized_conversation[-1]
            types = {p.original_value_data_type for p in message.message_pieces}
            if "image_path" in types:
                raise Exception("image not supported")
            return _ok_response()

        target._send_prompt_to_target_async = selective_send  # type: ignore[method-assign]

        result = await _discover_input_modalities_async(
            target=target,
            test_assets={"image_path": image_asset},
        )

        assert frozenset({"text"}) in result
        assert frozenset({"text", "image_path"}) not in result

    async def test_explicit_test_modalities_overrides_declared(self, image_asset: str) -> None:
        target = MockPromptTarget()
        # Declared as text-only, but caller asks us to probe text+image too.
        _set_input_modalities(target=target, modalities={frozenset({"text"})})
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        result = await _discover_input_modalities_async(
            target=target,
            test_modalities={frozenset({"text"}), frozenset({"text", "image_path"})},
            test_assets={"image_path": image_asset},
        )

        assert frozenset({"text"}) in result
        assert frozenset({"text", "image_path"}) in result

    async def test_combination_skipped_when_asset_missing(self, tmp_path: Path) -> None:
        target = MockPromptTarget()
        _set_input_modalities(target=target, modalities={frozenset({"text", "image_path"})})
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        # An explicit empty mapping disables the packaged defaults, so
        # image_path combinations are skipped instead of probed.
        result = await _discover_input_modalities_async(target=target, test_assets={})

        assert result == set()
        assert target._send_prompt_to_target_async.await_count == 0

    async def test_empty_test_modalities_returns_empty_without_probing(self) -> None:
        target = MockPromptTarget()
        _set_input_modalities(target=target, modalities={frozenset({"text"})})
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        result = await _discover_input_modalities_async(target=target, test_modalities=set())

        assert result == set()
        assert target._send_prompt_to_target_async.await_count == 0

    async def test_explicit_test_modalities_runs_under_permissive_configuration(self, image_asset: str) -> None:
        """
        Probing a modality combination the target does NOT declare must still
        succeed. Uses ``_RealValidationTarget`` so ``_validate_request`` runs
        and would reject the multi-piece, non-text payload were the
        permissive override absent.
        """
        target = _RealValidationTarget()
        send_mock = AsyncMock(return_value=_ok_response())
        target._send_prompt_to_target_async = send_mock  # type: ignore[method-assign]

        result = await _discover_input_modalities_async(
            target=target,
            test_modalities={frozenset({"text", "image_path"})},
            test_assets={"image_path": image_asset},
        )

        assert send_mock.await_count == 1
        assert frozenset({"text", "image_path"}) in result


@pytest.mark.usefixtures("patch_central_database")
class TestSendAndCheckTimeout:
    async def test_timeout_returns_false_after_retries(self) -> None:
        """
        When ``send_prompt_async`` exceeds ``per_probe_timeout_s``, the probe
        is treated as failed. ``_send_and_check_async`` retries once on
        timeout, so the underlying mock is awaited twice and the capability
        is excluded from the queried set.
        """
        target = MockPromptTarget()

        async def _hang(**_kwargs: object) -> list[Message]:
            await asyncio.sleep(10)
            return _ok_response()

        target._send_prompt_to_target_async = AsyncMock(side_effect=_hang)  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.JSON_OUTPUT},
            per_probe_timeout_s=0.01,
        )

        assert result == set()
        # One initial attempt plus one retry.
        assert target._send_prompt_to_target_async.await_count == 2


@pytest.mark.usefixtures("patch_central_database")
class TestSystemPromptProbeMemoryFailure:
    async def test_returns_false_when_memory_seed_raises(self) -> None:
        """
        If seeding the system message into memory raises (e.g. backend
        offline), the system-prompt probe returns False without attempting
        the user send.
        """
        target = MockPromptTarget()
        send_mock = AsyncMock(return_value=_ok_response())
        target._send_prompt_to_target_async = send_mock  # type: ignore[method-assign]

        with patch.object(target._memory, "add_message_to_memory", side_effect=RuntimeError("memory offline")):
            result = await _discover_capability_flags_async(
                target=target,
                capabilities={CapabilityName.SYSTEM_PROMPT},
            )

        assert result == set()
        # The user send is never attempted because seeding failed.
        send_mock.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
class TestVerifyTargetAsync:
    async def test_returns_target_capabilities_assembled_from_probes(self) -> None:
        """
        ``discover_target_capabilities_async`` runs both the capability and modality probes
        and assembles a ``TargetCapabilities`` populated from the
        queried results, copying ``output_modalities`` from the target's
        declared capabilities and deriving editable history conservatively.
        """
        declared = TargetCapabilities(
            input_modalities=frozenset({frozenset({"text"})}),
            output_modalities=frozenset({frozenset({"text"})}),
        )
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(capabilities=declared)
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        result = await discover_target_capabilities_async(target=target, per_probe_timeout_s=5.0)

        assert isinstance(result, TargetCapabilities)
        # Single-piece probes that don't touch memory always succeed when
        # the underlying send returns a clean response.
        assert result.supports_multi_message_pieces is True
        assert result.supports_json_schema is True
        assert result.supports_json_output is True
        # Editable history is conservative and therefore cannot remain true
        # when multi-turn support was not confirmed by probing.
        assert result.supports_editable_history is False
        # Modalities returned from the modality probe (text combination).
        assert frozenset({"text"}) in result.input_modalities
        # Output modalities copied through (not probed).
        assert result.output_modalities == declared.output_modalities

    async def test_excludes_capabilities_when_probe_send_fails(self) -> None:
        """
        When the underlying send raises, no capability or modality is
        queried, but ``supports_editable_history``, ``output_modalities``,
        and declared ``input_modalities`` are still preserved conservatively
        from the declared capabilities.
        """
        declared = TargetCapabilities(
            supports_editable_history=True,
            output_modalities=frozenset({frozenset({"text"})}),
        )
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(capabilities=declared)
        target._send_prompt_to_target_async = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

        result = await discover_target_capabilities_async(target=target, per_probe_timeout_s=0.5)

        assert result.supports_multi_turn is False
        assert result.supports_system_prompt is False
        assert result.supports_json_output is False
        assert result.supports_json_schema is False
        assert result.supports_multi_message_pieces is False
        # Editable history is derived conservatively and must fall when
        # multi-turn probing disproves the prerequisite capability.
        assert result.supports_editable_history is False
        # When probing cannot confirm modalities, declared modalities are
        # preserved (mirroring the boolean fallback semantics).
        assert result.input_modalities == declared.input_modalities
        # Output modalities still copied.
        assert result.output_modalities == declared.output_modalities

    async def test_empty_response_treated_as_failure(self) -> None:
        """A target returning an empty response list must NOT be reported as supporting probes."""
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=[])  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.JSON_OUTPUT, CapabilityName.MULTI_MESSAGE_PIECES},
        )

        assert result == set()

    async def test_response_with_no_pieces_treated_as_failure(self) -> None:
        """Responses whose Messages have no pieces must also be rejected."""
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(  # type: ignore[method-assign]
            return_value=[Message.__new__(Message)]
        )
        # Bypass __init__ to construct a Message with no pieces (Message.__init__ rejects empty).
        empty_msg = target._send_prompt_to_target_async.return_value[0]
        empty_msg.message_pieces = []

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.JSON_OUTPUT},
        )

        assert result == set()

    async def test_mixed_empty_message_in_response_treated_as_failure(self) -> None:
        """Any empty Message in a multi-message response must cause the probe to fail."""
        target = MockPromptTarget()
        ok = _ok_response()[0]
        empty = Message.__new__(Message)
        empty.message_pieces = []
        target._send_prompt_to_target_async = AsyncMock(return_value=[ok, empty])  # type: ignore[method-assign]

        result = await _discover_capability_flags_async(
            target=target,
            capabilities={CapabilityName.JSON_OUTPUT},
        )

        assert result == set()

    async def test_discover_target_capabilities_async_forwards_test_modalities(self, image_asset: str) -> None:
        declared = TargetCapabilities(input_modalities=frozenset({frozenset({"text"})}))
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(capabilities=declared)
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())

        extra_combo = frozenset({"text", "image_path"})
        result = await discover_target_capabilities_async(
            target=target,
            test_modalities={extra_combo},
            test_assets={"image_path": image_asset},
            per_probe_timeout_s=2.0,
        )

        # The undeclared combination is in the result only if test_modalities was forwarded.
        assert extra_combo in result.input_modalities

    async def test_discover_target_capabilities_async_preserves_declared_modalities_when_test_modalities_narrowed(
        self, image_asset: str
    ) -> None:
        declared_combo = frozenset({"text"})
        probed_combo = frozenset({"text", "image_path"})
        declared = TargetCapabilities(input_modalities=frozenset({declared_combo, probed_combo}))
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(capabilities=declared)
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())

        result = await discover_target_capabilities_async(
            target=target,
            test_modalities={probed_combo},
            test_assets={"image_path": image_asset},
            per_probe_timeout_s=2.0,
        )

        assert result.input_modalities == frozenset({declared_combo, probed_combo})

    async def test_discover_target_capabilities_async_forwards_capabilities(self) -> None:
        """``discover_target_capabilities_async`` must forward ``capabilities`` to narrow the probe set."""
        target = MockPromptTarget()
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        await discover_target_capabilities_async(
            target=target,
            capabilities={CapabilityName.JSON_OUTPUT},
            per_probe_timeout_s=2.0,
        )

        # Only the JSON_OUTPUT probe (1 send) and the modality probe(s) should run;
        # if `capabilities` were ignored, all 5 capability probes would fire (>= 6 sends
        # because multi-turn issues 2 sends).
        assert target._send_prompt_to_target_async.await_count <= 3

    async def test_discover_target_capabilities_async_preserves_declared_when_capabilities_narrowed(self) -> None:
        """
        When ``capabilities`` narrows the probe set, capabilities NOT in the
        narrowed set must fall back to the target's declared values rather
        than being silently reset to False.
        """
        declared = TargetCapabilities(
            supports_multi_turn=True,
            supports_system_prompt=True,
            supports_json_schema=True,
            supports_editable_history=True,
        )
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(capabilities=declared)
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        result = await discover_target_capabilities_async(
            target=target,
            capabilities={CapabilityName.JSON_OUTPUT},
            per_probe_timeout_s=2.0,
        )

        # The probed capability reflects the queried result.
        assert result.supports_json_output is True
        # Non-probed capabilities fall back to declared values.
        assert result.supports_multi_turn is True
        assert result.supports_system_prompt is True
        assert result.supports_json_schema is True
        assert result.supports_editable_history is True

    async def test_discover_target_capabilities_async_drops_editable_history_when_multi_turn_probe_fails(self) -> None:
        """Editable history must not remain true when probing disproves multi-turn support."""
        declared = TargetCapabilities(
            supports_multi_turn=True,
            supports_editable_history=True,
            output_modalities=frozenset({frozenset({"text"})}),
        )
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(capabilities=declared)

        async def selective_send(*, normalized_conversation: list[Message]) -> list[Message]:
            latest_text = normalized_conversation[-1].message_pieces[0].original_value
            if latest_text == "My favorite color is blue." or latest_text == "What did I just tell you?":
                raise RuntimeError("multi-turn unsupported")
            return _ok_response()

        target._send_prompt_to_target_async = AsyncMock(side_effect=selective_send)  # type: ignore[method-assign]

        result = await discover_target_capabilities_async(target=target, per_probe_timeout_s=2.0)

        assert result.supports_multi_turn is False
        assert result.supports_editable_history is False

    async def test_discover_target_capabilities_async_accepts_single_pass_iterable(self) -> None:
        declared = TargetCapabilities(
            supports_multi_turn=True,
            supports_editable_history=True,
        )
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(capabilities=declared)
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        gen = (c for c in [CapabilityName.JSON_OUTPUT, CapabilityName.EDITABLE_HISTORY])
        result = await discover_target_capabilities_async(
            target=target,
            capabilities=gen,
            per_probe_timeout_s=2.0,
        )

        assert result.supports_json_output is True
        assert result.supports_editable_history is True

    async def test_discover_target_capabilities_async_apply_installs_capabilities_on_target(self) -> None:
        """When ``apply=True``, the discovered capabilities are installed on the target."""
        declared = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(capabilities=declared)
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        assert target.capabilities.supports_multi_turn is False

        result = await discover_target_capabilities_async(target=target, per_probe_timeout_s=2.0, apply=True)

        assert target.capabilities == result
        assert target.capabilities.supports_multi_turn is True

    async def test_discover_target_capabilities_async_apply_defaults_to_false(self) -> None:
        """By default, ``discover_target_capabilities_async`` must not mutate the target."""
        declared = TargetCapabilities(supports_multi_turn=False, supports_system_prompt=False)
        target = MockPromptTarget()
        target._configuration = TargetConfiguration(capabilities=declared)
        target._send_prompt_to_target_async = AsyncMock(return_value=_ok_response())  # type: ignore[method-assign]

        result = await discover_target_capabilities_async(target=target, per_probe_timeout_s=2.0)

        # Result reflects probe; target capabilities remain at declared values.
        assert result.supports_multi_turn is True
        assert target.capabilities.supports_multi_turn is False


@pytest.mark.usefixtures("patch_central_database")
class TestMultiTurnProbeMemoryFailure:
    async def test_returns_false_when_history_seed_raises(self) -> None:
        """
        If seeding conversation history into memory raises, the multi-turn
        probe returns False rather than proceeding with a half-seeded
        conversation that would produce a false positive.
        """
        target = MockPromptTarget()
        send_mock = AsyncMock(return_value=_ok_response())
        target._send_prompt_to_target_async = send_mock  # type: ignore[method-assign]

        with patch.object(target._memory, "add_message_to_memory", side_effect=RuntimeError("memory offline")):
            result = await _discover_capability_flags_async(
                target=target,
                capabilities={CapabilityName.MULTI_TURN},
            )

        assert result == set()
        # The first turn ran (1 send); the second turn must NOT run because
        # seeding failed, otherwise the probe would falsely succeed.
        assert send_mock.await_count == 1
