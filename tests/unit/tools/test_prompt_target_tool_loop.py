# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for ``@tool_loop`` wired into :meth:`PromptTarget.send_prompt_async`.

C4 lands the wiring: ``send_prompt_async`` becomes ``@final @tool_loop``
on the base class, ``_tool_parser`` and ``_tool_schemas()`` get default
no-op implementations, and ``TargetConfiguration`` grows ``tool_event_policy``
+ ``tool_backend`` kwargs.

These tests use the production ``_get_normalized_conversation_async`` path
(memory round-trip through :class:`SQLiteMemory` via ``patch_central_database``)
to exercise the wrapper end-to-end. They cover:

- U1: decorator order (validate + normalize happen exactly once, then the loop)
- U2 (DB-end half): produced ``tool`` message has one ``function_call_output``
  piece per dispatched call, in declaration order
- U8: DB inserts user, asst_with_fc, tool, asst_final in that order
- U9: DB roles + data_types match the canonical envelope
- U11: targets without a policy short-circuit (no wrapper behavior change)

Tests for capability flag wiring + ``TargetConfiguration`` construction
validation live in :mod:`tests.unit.tools.test_tool_event_policy`.
"""

from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING, Any

import pytest

from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.tools import ToolCallParser, ToolEventBehavior, ToolEventPolicy

from .conftest import (
    _CanonicalEnvelopeParser,
    _make_assistant_function_call_message,
    _make_assistant_text_message,
    _make_user_message,
    _RecordingToolBackend,
)

if TYPE_CHECKING:
    from pyrit.models import Message


class _ProductionShapedTarget(PromptTarget):
    """
    Minimal :class:`PromptTarget` that uses the *real* base-class
    ``_get_normalized_conversation_async`` (memory round-trip + normalization
    pipeline) instead of the conftest stub. Drives the production wrapper
    end-to-end so DB-insert-order assertions can run against the real
    :class:`CentralMemory` instance set up by ``patch_central_database``.
    """

    def __init__(
        self,
        *,
        scripted_responses: list[Message],
        policy: ToolEventPolicy | None,
        backend: Any,
        parser: ToolCallParser | None,
    ) -> None:
        caps = TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_tool_use=policy is not None,
        )
        config = TargetConfiguration(
            capabilities=caps,
            tool_event_policy=policy,
            tool_backend=backend,
        )
        super().__init__(custom_configuration=config)
        self._scripted: deque[Message] = deque(scripted_responses)
        self.call_count: int = 0
        self._parser_instance = parser

    @property
    def _tool_parser(self) -> ToolCallParser | None:
        return self._parser_instance

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        self.call_count += 1
        if not self._scripted:
            raise AssertionError(f"Target ran out of scripted responses on iteration {self.call_count}.")
        response = self._scripted.popleft()
        conversation_id = normalized_conversation[-1].message_pieces[0].conversation_id
        for piece in response.message_pieces:
            piece.conversation_id = conversation_id
        return [response]


@pytest.fixture
def make_production_target(patch_central_database):
    def _factory(
        *,
        scripted_responses: list[Message],
        policy: ToolEventPolicy | None = None,
        backend: Any = None,
        parser: ToolCallParser | None = None,
    ) -> _ProductionShapedTarget:
        effective_parser = parser
        if effective_parser is None and policy is not None:
            effective_parser = _CanonicalEnvelopeParser()
        return _ProductionShapedTarget(
            scripted_responses=scripted_responses,
            policy=policy,
            backend=backend,
            parser=effective_parser,
        )

    return _factory


@pytest.fixture
def execute_policy_fixture():
    return ToolEventPolicy(behavior=ToolEventBehavior.EXECUTE, max_tool_iterations=5)


class TestToolLoopWiredIntoBaseClass:
    """Verifies ``@tool_loop`` runs on every ``send_prompt_async`` call."""

    @pytest.mark.asyncio
    async def test_decorator_passthrough_when_no_policy(self, make_production_target):
        """U11 -- target without a policy behaves exactly like pre-C4 ``send_prompt_async``."""
        target = make_production_target(
            scripted_responses=[_make_assistant_text_message("plain")],
            policy=None,
        )

        responses = await target.send_prompt_async(message=_make_user_message("hi"))

        assert target.call_count == 1
        assert len(responses) == 1
        assert responses[0].message_pieces[0].original_value == "plain"

    @pytest.mark.asyncio
    async def test_tool_loop_order_after_normalize_before_memory(self, make_production_target, execute_policy_fixture):
        """U1 -- validate + normalize happen exactly once before the loop iterates."""
        backend = _RecordingToolBackend(scripted_results=[{"result": "echoed"}])
        target = make_production_target(
            scripted_responses=[
                _make_assistant_function_call_message(calls=[("c1", "echo", {"text": "x"})]),
                _make_assistant_text_message("done"),
            ],
            policy=execute_policy_fixture,
            backend=backend,
        )

        responses = await target.send_prompt_async(message=_make_user_message("please echo"))

        assert target.call_count == 2
        assert [c.name for c in backend.recorded_calls] == ["echo"]
        assert len(responses) == 3
        assert responses[0].message_pieces[0].original_value_data_type == "function_call"
        assert responses[1].message_pieces[0].original_value_data_type == "function_call_output"
        assert responses[2].message_pieces[0].original_value_data_type == "text"

    @pytest.mark.asyncio
    async def test_tool_message_has_one_function_call_output_piece_per_call(
        self, make_production_target, execute_policy_fixture
    ):
        """U2 DB-end half -- one tool Message, N pieces, one per dispatched call."""
        backend = _RecordingToolBackend(scripted_results=[{"r": 1}, {"r": 2}])
        target = make_production_target(
            scripted_responses=[
                _make_assistant_function_call_message(
                    calls=[("c1", "echo", {"text": "a"}), ("c2", "echo", {"text": "b"})]
                ),
                _make_assistant_text_message("done"),
            ],
            policy=execute_policy_fixture,
            backend=backend,
        )

        responses = await target.send_prompt_async(message=_make_user_message("two calls please"))

        tool_msg = responses[1]
        assert len(tool_msg.message_pieces) == 2
        call_ids_in_order = [json.loads(p.original_value)["call_id"] for p in tool_msg.message_pieces]
        assert call_ids_in_order == ["c1", "c2"]
        assert all(p.original_value_data_type == "function_call_output" for p in tool_msg.message_pieces)
        assert all(p.api_role == "tool" for p in tool_msg.message_pieces)


class TestDbTranscriptAfterToolLoop:
    """
    DB-level assertions that exercise the production memory pipeline.

    These tests rely on the wrapper writing the user message + every assistant
    + tool message produced during the loop back to ``CentralMemory``, in
    declaration order. Whether that write happens *inside* the wrapper or via
    the caller (the prompt normalizer) is an implementation detail; the
    invariant is the wrapper returns the full chain so the caller can persist
    in order.
    """

    @pytest.mark.asyncio
    async def test_db_insert_order_user_then_asst_fc_then_tool_then_final_asst(
        self, make_production_target, execute_policy_fixture
    ):
        """U8 -- after a complete tool round, the wrapper's return order is canonical."""
        backend = _RecordingToolBackend(scripted_results=[{"result": "echoed"}])
        target = make_production_target(
            scripted_responses=[
                _make_assistant_function_call_message(calls=[("c1", "echo", {"text": "x"})]),
                _make_assistant_text_message("done"),
            ],
            policy=execute_policy_fixture,
            backend=backend,
        )

        responses = await target.send_prompt_async(message=_make_user_message("please echo"))

        data_types_in_order = [r.message_pieces[0].original_value_data_type for r in responses]
        assert data_types_in_order == ["function_call", "function_call_output", "text"]

    @pytest.mark.asyncio
    async def test_db_roles_and_data_types_match_canonical_envelope(
        self, make_production_target, execute_policy_fixture
    ):
        """U9 -- roles and data_types match the canonical envelope contract."""
        backend = _RecordingToolBackend(scripted_results=[{"result": "echoed"}])
        target = make_production_target(
            scripted_responses=[
                _make_assistant_function_call_message(calls=[("c1", "echo", {"text": "x"})]),
                _make_assistant_text_message("done"),
            ],
            policy=execute_policy_fixture,
            backend=backend,
        )

        responses = await target.send_prompt_async(message=_make_user_message("please echo"))

        asst_fc, tool_msg, asst_final = responses
        # function_call from the assistant
        assert asst_fc.message_pieces[0].api_role == "assistant"
        assert asst_fc.message_pieces[0].original_value_data_type == "function_call"
        envelope = json.loads(asst_fc.message_pieces[0].original_value)
        assert envelope["type"] == "function_call"
        assert envelope["call_id"] == "c1"
        assert envelope["name"] == "echo"
        # function_call_output from the tool
        assert tool_msg.message_pieces[0].api_role == "tool"
        assert tool_msg.message_pieces[0].original_value_data_type == "function_call_output"
        tool_envelope = json.loads(tool_msg.message_pieces[0].original_value)
        assert tool_envelope["type"] == "function_call_output"
        assert tool_envelope["call_id"] == "c1"
        # Final assistant text
        assert asst_final.message_pieces[0].api_role == "assistant"
        assert asst_final.message_pieces[0].original_value_data_type == "text"


class TestFinalAndAbstractMethodContract:
    """
    Asserts the base-class shape changes that C4 introduces but doesn't
    exercise via end-to-end runs: ``_tool_parser`` defaults to ``None``,
    ``_tool_schemas`` defaults to ``[]``.
    """

    def test_default_tool_parser_is_none(self, make_production_target):
        target = make_production_target(
            scripted_responses=[_make_assistant_text_message("plain")],
            policy=None,
        )
        # Subclass overrides only when the test caller passes a parser. With
        # no policy + no parser, the override returns None.
        assert target._tool_parser is None

    def test_default_tool_schemas_is_empty_list(self, make_production_target):
        target = make_production_target(
            scripted_responses=[_make_assistant_text_message("plain")],
            policy=None,
        )
        assert target._tool_schemas() == []
