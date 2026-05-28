# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for :func:`pyrit.tools.tool_loop`.

Coverage map (rows from the C2 test matrix):

* **U2** (partial; full-DB end lands in C5) — ``test_loop_returns_full_chain_in_order``
* **U3** — ``test_loop_exits_on_first_response_when_no_tool_calls``,
  ``test_loops_until_no_pending_tool_call``
* **U4** — ``test_raises_after_max_tool_iterations``,
  ``test_partial_conversation_attached_to_limit_exception``
* **U12** — ``test_policy_raise_includes_partial_conversation``
* **U13** — ``test_policy_return_raw_does_not_dispatch``
* **U16** — ``test_multi_call_per_turn_dispatched_sequentially_in_order``

Also covers two additional decorator concerns required by the rubber-duck
review (§10): EXECUTE policy with no backend raises with a partial
conversation, and the normalized conversation grows correctly across
iterations (decorator does not re-normalize each turn).
"""

from __future__ import annotations

import json

import pytest

from pyrit.exceptions import ToolCallLoopLimitExceeded, ToolCallNotSupported
from pyrit.tools import ToolEventBehavior, ToolEventPolicy

from .conftest import (
    _make_assistant_function_call_message,
    _make_assistant_text_message,
    _make_user_message,
)


@pytest.mark.usefixtures("patch_central_database")
class TestToolLoopDecoratorBasics:
    """Loop entry/exit semantics: no tool calls, single round trip, multi-round."""

    async def test_loop_exits_on_first_response_when_no_tool_calls(self, make_fake_target, execute_policy):
        target = make_fake_target(
            scripted_responses=[_make_assistant_text_message("done")],
            policy=execute_policy(),
        )

        responses = await target.send_prompt_async(message=_make_user_message("hi"))

        assert len(responses) == 1
        assert responses[0].get_value() == "done"
        assert target.call_count == 1

    async def test_loops_until_no_pending_tool_call(self, make_fake_target, execute_policy, recording_backend):
        backend = recording_backend(scripted_results=[{"ok": True}, {"ok": True}])
        target = make_fake_target(
            scripted_responses=[
                _make_assistant_function_call_message(calls=[("c1", "tool_a", {"x": 1})]),
                _make_assistant_function_call_message(calls=[("c2", "tool_a", {"x": 2})]),
                _make_assistant_text_message("done"),
            ],
            policy=execute_policy(max_tool_iterations=5),
            backend=backend,
        )

        responses = await target.send_prompt_async(message=_make_user_message("hi"))

        # Two model-tool round trips and one final assistant message.
        assert target.call_count == 3
        # Returned chain: fc1, tool1, fc2, tool2, final-text → 5 messages total.
        assert len(responses) == 5
        assert [r.message_pieces[0].original_value_data_type for r in responses] == [
            "function_call",
            "function_call_output",
            "function_call",
            "function_call_output",
            "text",
        ]
        assert len(backend.recorded_calls) == 2
        assert [c.call_id for c in backend.recorded_calls] == ["c1", "c2"]


@pytest.mark.usefixtures("patch_central_database")
class TestToolLoopMessageShape:
    """U2 — assistant_fc → tool → final_assistant ordering and identity."""

    async def test_loop_returns_full_chain_in_order(self, make_fake_target, execute_policy, recording_backend):
        backend = recording_backend(scripted_results=[{"weather": "sunny"}])
        fc_msg = _make_assistant_function_call_message(calls=[("call_abc", "get_weather", {"city": "Seattle"})])
        final_msg = _make_assistant_text_message("It is sunny in Seattle.")

        target = make_fake_target(
            scripted_responses=[fc_msg, final_msg],
            policy=execute_policy(),
            backend=backend,
        )

        responses = await target.send_prompt_async(message=_make_user_message("weather?"))

        assert len(responses) == 3
        # 1) assistant with function_call (identity preserved)
        assert responses[0] is fc_msg
        # 2) tool message with exactly one function_call_output piece carrying call_id
        tool_msg = responses[1]
        assert len(tool_msg.message_pieces) == 1
        tool_piece = tool_msg.message_pieces[0]
        assert tool_piece.api_role == "tool"
        assert tool_piece.original_value_data_type == "function_call_output"
        envelope = json.loads(tool_piece.original_value)
        assert envelope["type"] == "function_call_output"
        assert envelope["call_id"] == "call_abc"
        # The tool result is JSON-serialized into the "output" field.
        assert json.loads(envelope["output"]) == {"weather": "sunny"}
        # 3) final assistant text (identity preserved)
        assert responses[2] is final_msg


@pytest.mark.usefixtures("patch_central_database")
class TestToolLoopIterationLimits:
    """U4 — iteration cap raises and carries the partial chain."""

    async def test_raises_after_max_tool_iterations(self, make_fake_target, execute_policy, recording_backend):
        # Model never stops asking for tools.
        backend = recording_backend(scripted_results=[{"ok": True}] * 3)
        target = make_fake_target(
            scripted_responses=[
                _make_assistant_function_call_message(calls=[(f"c{i}", "loop_tool", {})]) for i in range(3)
            ],
            policy=execute_policy(max_tool_iterations=2),
            backend=backend,
        )

        with pytest.raises(ToolCallLoopLimitExceeded, match="max_tool_iterations=2"):
            await target.send_prompt_async(message=_make_user_message("hi"))

        # Exactly max_tool_iterations model calls made before raising.
        assert target.call_count == 2

    async def test_partial_conversation_attached_to_limit_exception(
        self, make_fake_target, execute_policy, recording_backend
    ):
        backend = recording_backend(scripted_results=[{"ok": True}] * 2)
        target = make_fake_target(
            scripted_responses=[
                _make_assistant_function_call_message(calls=[(f"c{i}", "loop_tool", {})]) for i in range(2)
            ],
            policy=execute_policy(max_tool_iterations=2),
            backend=backend,
        )

        with pytest.raises(ToolCallLoopLimitExceeded) as excinfo:
            await target.send_prompt_async(message=_make_user_message("hi"))

        partial = excinfo.value.partial_conversation
        # 2 iterations × (assistant_fc + tool_msg) = 4 messages, all in order.
        assert len(partial) == 4
        assert [m.message_pieces[0].original_value_data_type for m in partial] == [
            "function_call",
            "function_call_output",
            "function_call",
            "function_call_output",
        ]


@pytest.mark.usefixtures("patch_central_database")
class TestToolEventPolicyBehaviors:
    """U12, U13 — non-EXECUTE behaviors short-circuit dispatch."""

    async def test_policy_raise_includes_partial_conversation(self, make_fake_target, recording_backend):
        backend = recording_backend(scripted_results=[{"ok": True}])
        fc_msg = _make_assistant_function_call_message(calls=[("c1", "danger", {})])
        target = make_fake_target(
            scripted_responses=[fc_msg],
            policy=ToolEventPolicy(behavior=ToolEventBehavior.RAISE),
            backend=backend,
        )

        with pytest.raises(ToolCallNotSupported, match="RAISE") as excinfo:
            await target.send_prompt_async(message=_make_user_message("hi"))

        partial = excinfo.value.partial_conversation
        # Partial contains the offending assistant turn; no tool dispatch occurred.
        assert partial == [fc_msg]
        assert backend.recorded_calls == []
        assert target.call_count == 1

    async def test_policy_return_raw_does_not_dispatch(self, make_fake_target, recording_backend):
        backend = recording_backend(scripted_results=[{"ok": True}])
        fc_msg = _make_assistant_function_call_message(calls=[("c1", "danger", {})])
        target = make_fake_target(
            scripted_responses=[fc_msg],
            policy=ToolEventPolicy(behavior=ToolEventBehavior.RETURN_RAW),
            backend=backend,
        )

        responses = await target.send_prompt_async(message=_make_user_message("hi"))

        assert responses == [fc_msg]
        assert backend.recorded_calls == []
        assert target.call_count == 1


@pytest.mark.usefixtures("patch_central_database")
class TestToolLoopMultiCallPerTurn:
    """U16 — multi-call turns dispatch sequentially in declaration order."""

    async def test_multi_call_per_turn_dispatched_sequentially_in_order(
        self, make_fake_target, execute_policy, recording_backend
    ):
        backend = recording_backend(scripted_results=[{"a": 1}, {"b": 2}, {"c": 3}])
        multi_fc = _make_assistant_function_call_message(
            calls=[
                ("c_alpha", "tool_alpha", {"k": "v1"}),
                ("c_beta", "tool_beta", {"k": "v2"}),
                ("c_gamma", "tool_gamma", {"k": "v3"}),
            ]
        )
        target = make_fake_target(
            scripted_responses=[multi_fc, _make_assistant_text_message("ok")],
            policy=execute_policy(),
            backend=backend,
        )

        responses = await target.send_prompt_async(message=_make_user_message("multi"))

        # Three calls dispatched in declaration order, recorded ids match.
        assert [c.call_id for c in backend.recorded_calls] == ["c_alpha", "c_beta", "c_gamma"]
        assert [c.name for c in backend.recorded_calls] == ["tool_alpha", "tool_beta", "tool_gamma"]
        # One tool message after the multi-call assistant turn, carrying three
        # function_call_output pieces in declaration order with the right call_ids.
        tool_msg = responses[1]
        assert len(tool_msg.message_pieces) == 3
        envelopes = [json.loads(p.original_value) for p in tool_msg.message_pieces]
        assert [e["call_id"] for e in envelopes] == ["c_alpha", "c_beta", "c_gamma"]
        assert all(p.original_value_data_type == "function_call_output" for p in tool_msg.message_pieces)


@pytest.mark.usefixtures("patch_central_database")
class TestToolLoopMisconfiguration:
    """EXECUTE policy with no backend must fail loudly and carry the partial chain."""

    async def test_execute_without_backend_raises_with_partial(self, make_fake_target, execute_policy):
        fc_msg = _make_assistant_function_call_message(calls=[("c1", "no_reg", {})])
        target = make_fake_target(
            scripted_responses=[fc_msg],
            policy=execute_policy(),
            backend=None,
        )

        with pytest.raises(ToolCallNotSupported, match="tool_backend") as excinfo:
            await target.send_prompt_async(message=_make_user_message("hi"))

        assert excinfo.value.partial_conversation == [fc_msg]


@pytest.mark.usefixtures("patch_central_database")
class TestToolLoopConversationGrowth:
    """The decorator must extend (not re-normalize) the conversation each round."""

    async def test_normalized_conversation_grows_each_iteration(
        self, make_fake_target, execute_policy, recording_backend
    ):
        backend = recording_backend(scripted_results=[{"r1": 1}, {"r2": 2}])
        target = make_fake_target(
            scripted_responses=[
                _make_assistant_function_call_message(calls=[("c1", "t", {})]),
                _make_assistant_function_call_message(calls=[("c2", "t", {})]),
                _make_assistant_text_message("done"),
            ],
            policy=execute_policy(),
            backend=backend,
        )

        await target.send_prompt_async(message=_make_user_message("hi"))

        # Three protected-method calls; each subsequent call sees the prior
        # assistant_fc + tool_msg appended (the decorator must NOT re-normalize).
        seen = target.normalized_conversations_seen
        assert len(seen) == 3
        # call 1: just the user message
        assert len(seen[0]) == 1
        # call 2: user + assistant_fc(c1) + tool_msg
        assert len(seen[1]) == 3
        assert seen[1][1].message_pieces[0].original_value_data_type == "function_call"
        assert seen[1][2].message_pieces[0].original_value_data_type == "function_call_output"
        # call 3: user + assistant_fc(c1) + tool_msg + assistant_fc(c2) + tool_msg
        assert len(seen[2]) == 5
