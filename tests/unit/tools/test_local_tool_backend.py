# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for :class:`pyrit.tools.LocalToolBackend`.

Coverage map (rows from the C2 test matrix):

* **U10** (partial; the MCP counterpart lands in C3) —
  ``test_each_dummy_tool_invoked_via_prepended_conversation``
* **U17** (partial; the MCP-timeout counterpart lands in C3) —
  ``test_failing_tool_yields_error_envelope``
* **U18** — ``test_disallowed_tool_returns_error_without_invoking_callable``

Also covers the backend's documented behavior for missing functions
(both strict and tolerant modes), schema property defaulting, scalar
result wrapping, and declaration-order preservation in the bulk dispatch
path. These are required for the §10 rubber-duck guarantee that every
public-facing branch of :class:`LocalToolBackend` is exercised
before C5 wires it to a production target.
"""

from __future__ import annotations

import pytest

from pyrit.tools import LocalToolBackend, ToolCall


def _make_call(name: str, *, call_id: str = "c1", arguments: dict | None = None) -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments=arguments or {})


async def test_disallowed_tool_returns_error_without_invoking_callable():
    invoked: list[str] = []

    async def echo(args: dict) -> dict:
        invoked.append(args.get("text", ""))
        return {"echoed": args.get("text", "")}

    backend = LocalToolBackend(
        callables={"echo": echo, "off_limits": echo},
        allowed_tools={"echo"},
    )

    result = await backend.dispatch_async(_make_call("off_limits", arguments={"text": "nope"}))

    assert result["error"] == "tool_not_allowed"
    assert result["tool"] == "off_limits"
    assert "echo" in result["allowed_tools"]
    assert invoked == []  # callable was never invoked


async def test_failing_tool_yields_error_envelope():
    async def boom(args: dict) -> dict:
        raise RuntimeError("kaboom")

    backend = LocalToolBackend(callables={"boom": boom})

    result = await backend.dispatch_async(_make_call("boom"))

    assert result["error"] == "tool_execution_failed"
    assert result["tool"] == "boom"
    assert "kaboom" in result["detail"]


async def test_missing_tool_raises_when_strict():
    backend = LocalToolBackend(callables={}, fail_on_missing_function=True)

    with pytest.raises(KeyError, match="ghost"):
        await backend.dispatch_async(_make_call("ghost"))


async def test_missing_tool_returns_envelope_when_tolerant():
    async def echo(args: dict) -> dict:
        return {"ok": True}

    backend = LocalToolBackend(
        callables={"echo": echo},
        fail_on_missing_function=False,
    )

    result = await backend.dispatch_async(_make_call("ghost"))

    assert result["error"] == "tool_not_registered"
    assert result["tool"] == "ghost"
    assert result["available_tools"] == ["echo"]


async def test_scalar_result_is_wrapped_in_dict():
    async def number(args: dict) -> int:
        return 42

    backend = LocalToolBackend(callables={"number": number})

    result = await backend.dispatch_async(_make_call("number"))

    assert result == {"result": 42}


async def test_dict_result_passes_through_unchanged():
    async def named(args: dict) -> dict:
        return {"custom_key": "custom_value"}

    backend = LocalToolBackend(callables={"named": named})

    result = await backend.dispatch_async(_make_call("named"))

    assert result == {"custom_key": "custom_value"}


async def test_schemas_defaults_to_empty_list():
    backend = LocalToolBackend(callables={})

    assert backend.schemas == []


async def test_schemas_returned_as_copy():
    schemas_in = [{"name": "echo", "parameters": {}}]
    backend = LocalToolBackend(callables={}, schemas=schemas_in)

    out1 = backend.schemas
    out1.append({"name": "mutated"})

    # Mutating the returned list does not affect the backend's internal state.
    assert backend.schemas == schemas_in


async def test_dispatch_all_sequential_preserves_declaration_order():
    async def echo(args: dict) -> dict:
        return {"echoed": args["i"]}

    backend = LocalToolBackend(callables={"echo": echo})

    calls = [_make_call("echo", call_id=f"c{i}", arguments={"i": i}) for i in range(5)]
    pairs = await backend.dispatch_all_sequential_async(calls)

    assert [c.call_id for c, _ in pairs] == ["c0", "c1", "c2", "c3", "c4"]
    assert [r["echoed"] for _, r in pairs] == [0, 1, 2, 3, 4]


async def test_each_dummy_tool_invoked_via_prepended_conversation():
    """
    U10 (partial). Each dummy tool resolves on first dispatch (single
    forward step, no model reasoning trace), confirming the backend can
    short-circuit a prepended conversation where every call is already
    decided. The MCP counterpart in C3 exercises the same shape against
    a real stdio server.
    """
    invocations: list[tuple[str, dict]] = []

    async def echo(args: dict) -> dict:
        invocations.append(("echo", args))
        return {"echoed": args.get("text", "")}

    async def add(args: dict) -> dict:
        invocations.append(("add", args))
        return {"sum": args["a"] + args["b"]}

    async def reverse(args: dict) -> dict:
        invocations.append(("reverse", args))
        return {"reversed": args.get("text", "")[::-1]}

    backend = LocalToolBackend(callables={"echo": echo, "add": add, "reverse": reverse})

    prepended_calls = [
        _make_call("echo", call_id="e1", arguments={"text": "hello"}),
        _make_call("add", call_id="a1", arguments={"a": 2, "b": 3}),
        _make_call("reverse", call_id="r1", arguments={"text": "pyrit"}),
    ]
    pairs = await backend.dispatch_all_sequential_async(prepended_calls)

    # Each dummy resolved exactly once; no retries, no model re-entry.
    assert len(invocations) == 3
    assert [name for name, _ in invocations] == ["echo", "add", "reverse"]
    assert [r for _, r in pairs] == [
        {"echoed": "hello"},
        {"sum": 5},
        {"reversed": "tiryp"},
    ]
