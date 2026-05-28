# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for the wiring between :class:`TargetCapabilities.supports_tool_use`,
:class:`TargetConfiguration.tool_event_policy` /
:class:`TargetConfiguration.tool_backend`, and the
:func:`pyrit.tools.tool_loop` decorator that lives on
:class:`PromptTarget.send_prompt_async`.

These tests are the §7 U7 row plus the construction-time validator added in C4.
They assert the *capability flag* axis only -- that targets which declare
``supports_tool_use=True`` and configure a policy + backend route through
the loop, that targets without a policy short-circuit, and that the
``tool_backend``-without-capability misconfiguration raises at construction.

End-to-end ordering against the production memory pipeline (U1, U8, U9) is
exercised separately in ``tests/unit/prompt_target/common/test_prompt_target_tool_loop.py``.
"""

from __future__ import annotations

import pytest

from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.tools import LocalToolBackend, ToolEventBehavior, ToolEventPolicy

from .conftest import (
    _make_assistant_function_call_message,
    _make_assistant_text_message,
    _make_user_message,
)


class TestSupportsToolUseCapabilityFlag:
    """Asserts the new ``supports_tool_use`` field on :class:`TargetCapabilities`."""

    def test_default_is_false(self):
        caps = TargetCapabilities()
        assert caps.supports_tool_use is False

    def test_explicit_true(self):
        caps = TargetCapabilities(supports_tool_use=True)
        assert caps.supports_tool_use is True


class TestTargetConfigurationToolFields:
    """Asserts the new ``tool_event_policy`` / ``tool_backend`` kwargs."""

    def test_defaults_are_none(self):
        caps = TargetCapabilities(supports_tool_use=True)
        config = TargetConfiguration(capabilities=caps)
        assert config.tool_event_policy is None
        assert config.tool_backend is None

    def test_explicit_policy_and_backend(self):
        caps = TargetCapabilities(supports_tool_use=True)
        backend = LocalToolBackend(callables={}, schemas=[])
        policy = ToolEventPolicy(behavior=ToolEventBehavior.EXECUTE)
        config = TargetConfiguration(
            capabilities=caps,
            tool_event_policy=policy,
            tool_backend=backend,
        )
        assert config.tool_event_policy is policy
        assert config.tool_backend is backend

    def test_tool_backend_without_capability_raises(self):
        caps = TargetCapabilities(supports_tool_use=False)
        backend = LocalToolBackend(callables={}, schemas=[])
        with pytest.raises(ValueError, match="supports_tool_use"):
            TargetConfiguration(capabilities=caps, tool_backend=backend)

    def test_tool_event_policy_without_backend_is_allowed(self):
        """``RAISE`` / ``RETURN_RAW`` policies do not require a backend."""
        caps = TargetCapabilities(supports_tool_use=True)
        policy = ToolEventPolicy(behavior=ToolEventBehavior.RAISE)
        config = TargetConfiguration(capabilities=caps, tool_event_policy=policy)
        assert config.tool_event_policy is policy
        assert config.tool_backend is None


class TestCapabilityFlagWiringIntoToolLoop:
    """
    U7 -- verify the wrapper dispatches only when the target declares
    ``supports_tool_use`` AND a policy is configured.
    """

    @pytest.mark.asyncio
    async def test_target_with_tool_use_capability_uses_tool_loop(
        self, make_fake_target, recording_backend, execute_policy
    ):
        backend = recording_backend()
        target = make_fake_target(
            scripted_responses=[
                _make_assistant_function_call_message(calls=[("c1", "echo", {"text": "hi"})]),
                _make_assistant_text_message("done"),
            ],
            policy=execute_policy(),
            backend=backend,
        )

        responses = await target.send_prompt_async(message=_make_user_message("please call echo"))

        assert target.call_count == 2, "Decorator should have iterated twice (call + final)."
        assert [c.name for c in backend.recorded_calls] == ["echo"]
        assert len(responses) == 3, "user expects asst_fc, tool_msg, asst_final."

    @pytest.mark.asyncio
    async def test_target_without_tool_use_capability_skips_dispatch(self, make_fake_target):
        target = make_fake_target(
            scripted_responses=[_make_assistant_text_message("plain response, no tool call")],
            policy=None,
            backend=None,
        )

        responses = await target.send_prompt_async(message=_make_user_message("hello"))

        assert target.call_count == 1
        assert len(responses) == 1
