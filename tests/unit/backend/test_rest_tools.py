# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request

from pyrit import _compatibility
from pyrit.backend.main import app as backend_app
from pyrit.backend.rest_tools import RestToolset
from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.prompt_target import OpenAIResponseTarget
from pyrit.registry import ScorerRegistry, TargetRegistry
from pyrit.score import SubStringScorer

COMPATIBILITY_ID = "0.0.0+g" + "a" * 40

# Tool names come from route function names, so renaming a route must fail here, not silently rename a tool.
BACKEND_TOOLS = [
    ("list_targets", "GET", "/api/targets"),
    ("list_scenarios", "GET", "/api/scenarios/catalog"),
    ("get_scenario", "GET", "/api/scenarios/catalog/{scenario_name}"),
    ("list_techniques", "GET", "/api/techniques"),
    ("list_datasets", "GET", "/api/datasets"),
    ("list_scorers", "GET", "/api/scorers"),
    ("start_scenario_run", "POST", "/api/scenarios/runs"),
    ("list_scenario_runs", "GET", "/api/scenarios/runs"),
    ("get_scenario_run", "GET", "/api/scenarios/runs/{scenario_result_id}"),
    ("get_scenario_run_progress", "GET", "/api/scenarios/runs/{scenario_result_id}/progress"),
    ("list_attacks", "GET", "/api/attacks"),
    ("get_attack", "GET", "/api/attacks/{attack_result_id}"),
    ("get_conversation_messages", "GET", "/api/attacks/{attack_result_id}/messages"),
    ("query_analytics", "POST", "/api/analytics/attacks/query"),
]


@pytest.fixture
def toolset() -> Iterator[RestToolset]:
    app = FastAPI()
    app.state.compatibility_id = COMPATIBILITY_ID

    @app.get("/api/things/{thing_id}")
    async def get_thing_async(thing_id: str, request: Request) -> dict[str, object]:
        """Get one thing."""
        if thing_id == "missing":
            raise HTTPException(status_code=404, detail="Thing 'missing' not found")
        return {
            "id": thing_id,
            "authorization": request.headers.get("authorization"),
            "compatibility_id": request.headers.get(_compatibility.COMPATIBILITY_HEADER),
            "identifier": {
                "class_name": "Thing",
                "hash": "abc",
                "eval_hash": "def",
                "model_name": "m",
                "children": {"x": {}},
            },
            "note": None,
            # An analytics bucket for an empty label: its empty value is real, not missing.
            "bucket": {"kind": "value", "value": ""},
        }

    @app.get("/api/things")
    async def list_things(limit: int = 50) -> dict[str, object]:
        """List things."""
        return {
            "items": [{"name": f"thing-{index}", "text": "x" * 50} for index in range(limit)],
            "pagination": {"limit": limit, "has_more": True, "next_cursor": "after-the-page"},
        }

    @app.get("/api/notes")
    async def get_notes(count: int = 1, length: int = 10, cursor_field: str = "next_cursor") -> dict[str, object]:
        """Get notes in a list that is not named items, with a continuation cursor."""
        return {"notebook": "n", "messages": [{"text": "m" * length}] * count, cursor_field: "after-the-page"}

    @app.get("/api/conversation")
    async def get_conversation() -> dict[str, object]:
        """Get a conversation whose first message alone is over the response limit."""
        return {"messages": [{"text": "q" * 50_000}, {"text": "Sure, here is the answer."}]}

    with patch.object(RestToolset, "OPERATIONS", ("get_thing", "list_things", "get_notes", "get_conversation")):
        yield RestToolset(app=app)


def test_tools_are_rest_operations_named_after_their_route_functions(toolset: RestToolset) -> None:
    operations = {operation.name: operation for operation in toolset.operations}

    assert list(operations) == ["get_thing", "list_things", "get_notes", "get_conversation"]
    assert operations["get_thing"].description.startswith("GET /api/things/{thing_id}\n\nGet one thing.")
    assert operations["get_thing"].input_schema["required"] == ["thing_id"]
    assert set(operations["list_things"].input_schema["properties"]) == {"limit", RestToolset.RESPONSE_FIELDS}


async def test_call_forwards_credentials_and_compacts_identifiers(toolset: RestToolset) -> None:
    text, failed = await toolset.call_async(name="get_thing", arguments={"thing_id": "a?b"}, authorization="Bearer t")

    assert failed is False
    assert json.loads(text) == {
        "id": "a?b",
        "authorization": "Bearer t",
        "compatibility_id": COMPATIBILITY_ID,
        "identifier": {"class_name": "Thing", "hash": "abc", "model_name": "m"},
        "bucket": {"kind": "value", "value": ""},
    }


async def test_error_response_is_returned_whole_as_a_failed_result(toolset: RestToolset) -> None:
    text, failed = await toolset.call_async(
        name="get_thing", arguments={"thing_id": "missing", RestToolset.RESPONSE_FIELDS: ["id"]}, authorization=None
    )

    assert failed is True
    assert json.loads(text) == {"status": 404, "error": {"detail": "Thing 'missing' not found"}}


@pytest.mark.parametrize(
    ("name", "arguments", "error"),
    [
        ("no_such_tool", {}, "Unknown tool 'no_such_tool'."),
        ("get_thing", {"thing_id": None}, "Missing path parameters: thing_id."),
        ("get_thing", {"thing_id": "a/secret"}, "Path parameters cannot contain '/': thing_id."),
    ],
)
async def test_call_reports_unknown_tools_and_missing_path_parameters(
    toolset: RestToolset, name: str, arguments: dict[str, object], error: str
) -> None:
    text, failed = await toolset.call_async(name=name, arguments=arguments, authorization=None)

    assert failed is True
    assert json.loads(text) == {"error": error}


async def test_large_list_keeps_leading_items_without_a_cursor_past_the_cut_ones(toolset: RestToolset) -> None:
    with patch.object(RestToolset, "MAX_RESPONSE_CHARS", 1_000):
        text, _ = await toolset.call_async(name="list_things", arguments={"limit": 100}, authorization=None)

    result = json.loads(text)
    kept = len(result["items"])
    assert len(text) <= 1_000
    assert kept > 0
    assert [item["name"] for item in result["items"]] == [f"thing-{index}" for index in range(kept)]
    assert result["items_omitted"] == 100 - kept
    # The cursor continues after thing-99, so following it would skip every cut item.
    assert result["pagination"] == {"limit": 100, "has_more": True}


@pytest.mark.parametrize("cursor_field", ["next_cursor", "next_group_offset"])
async def test_large_response_cuts_its_largest_list_and_stays_valid_json(
    toolset: RestToolset, cursor_field: str
) -> None:
    with patch.object(RestToolset, "MAX_RESPONSE_CHARS", 1_000):
        text, _ = await toolset.call_async(
            name="get_notes", arguments={"count": 30, "length": 100, "cursor_field": cursor_field}, authorization=None
        )

    result = json.loads(text)
    assert len(text) <= 1_000
    assert result["notebook"] == "n"
    assert 0 < len(result["messages"]) < 30
    assert result["messages_omitted"] == 30 - len(result["messages"])
    assert cursor_field not in result


async def test_response_too_large_to_cut_names_its_fields(toolset: RestToolset) -> None:
    with patch.object(RestToolset, "MAX_RESPONSE_CHARS", 1_000):
        text, _ = await toolset.call_async(name="get_notes", arguments={"length": 990}, authorization=None)

    result = json.loads(text)
    assert len(text) <= 1_000
    assert result["available_fields"] == ["messages", "next_cursor", "notebook"]
    assert "response_fields" in result["note"]


async def test_long_text_is_a_preview_unless_its_field_is_requested(toolset: RestToolset) -> None:
    preview, _ = await toolset.call_async(name="get_notes", arguments={"length": 1_500}, authorization=None)
    full, _ = await toolset.call_async(
        name="get_notes",
        arguments={"length": 1_500, RestToolset.RESPONSE_FIELDS: ["messages.text"]},
        authorization=None,
    )

    assert json.loads(preview)["messages"] == [{"text": "m" * 1_000 + "…"}]
    assert json.loads(full) == {"messages": [{"text": "m" * 1_500}]}


async def test_named_long_text_that_does_not_fit_comes_back_as_a_preview(toolset: RestToolset) -> None:
    text, _ = await toolset.call_async(
        name="get_conversation", arguments={RestToolset.RESPONSE_FIELDS: ["messages.text"]}, authorization=None
    )

    # The reply after an oversized prompt stays readable instead of the whole response becoming a note.
    assert json.loads(text) == {"messages": [{"text": "q" * 1_000 + "…"}, {"text": "Sure, here is the answer."}]}


async def test_response_fields_keep_matches_and_name_alternatives_for_misses(toolset: RestToolset) -> None:
    text, _ = await toolset.call_async(
        name="list_things",
        arguments={"limit": 2, RestToolset.RESPONSE_FIELDS: ["items.name", "items.title"]},
        authorization=None,
    )

    assert json.loads(text) == {
        "items": [{"name": "thing-0"}, {"name": "thing-1"}],
        "unmatched_response_fields": {"items.title": {"available_fields": ["name", "text"]}},
    }


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("get_thing", {"thing_id": "a", RestToolset.RESPONSE_FIELDS: ["id", "note"]}, {"id": "a"}),
        ("get_thing", {"thing_id": "a", RestToolset.RESPONSE_FIELDS: ["id", "note.text"]}, {"id": "a"}),
        ("list_things", {"limit": 0, RestToolset.RESPONSE_FIELDS: ["items.name"]}, {}),
    ],
)
async def test_response_fields_do_not_report_fields_left_out_for_being_empty(
    toolset: RestToolset, name: str, arguments: dict[str, object], expected: dict[str, object]
) -> None:
    text, _ = await toolset.call_async(name=name, arguments=arguments, authorization=None)

    assert json.loads(text) == expected


async def test_response_fields_report_a_field_the_response_never_returns(toolset: RestToolset) -> None:
    text, _ = await toolset.call_async(
        name="get_thing",
        arguments={"thing_id": "a", RestToolset.RESPONSE_FIELDS: ["identifier.eval_hash"]},
        authorization=None,
    )

    assert json.loads(text) == {
        "unmatched_response_fields": {
            "identifier.eval_hash": {"available_fields": ["class_name", "hash", "model_name"]}
        }
    }


def test_missing_offered_operation_is_reported(toolset: RestToolset) -> None:
    with (
        patch.object(RestToolset, "OPERATIONS", ("get_thing", "no_such_route")),
        pytest.raises(ValueError, match="no_such_route"),
    ):
        _ = toolset.operations


def test_backend_offers_the_selected_operations() -> None:
    operations = RestToolset(app=backend_app).operations

    assert [(operation.name, operation.method, operation.path) for operation in operations] == BACKEND_TOOLS


def _references(node: object) -> list[str]:
    if isinstance(node, dict):
        own = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        return own + [reference for value in node.values() for reference in _references(value)]
    if isinstance(node, list):
        return [reference for value in node for reference in _references(value)]
    return []


def test_backend_tool_schemas_are_self_contained() -> None:
    schemas = [operation.input_schema for operation in RestToolset(app=backend_app).operations]

    # MCP clients get only the tool schema, so every reference must resolve inside it.
    assert any(_references(schema) for schema in schemas)
    for schema in schemas:
        for reference in _references(schema):
            assert reference.startswith("#/$defs/")
            assert reference.removeprefix("#/$defs/") in schema["$defs"]


async def test_backend_tool_calls_the_real_api(patch_central_database: MagicMock) -> None:
    TargetRegistry.reset_registry_singleton()
    target = OpenAIResponseTarget(model_name="gpt-4", endpoint="https://example.invalid", api_key="not-a-key")
    TargetRegistry.get_registry_singleton().instances.register(target, name="model-under-test")
    try:
        text, failed = await RestToolset(app=backend_app).call_async(
            name="list_targets", arguments={}, authorization=None
        )
    finally:
        TargetRegistry.reset_registry_singleton()

    assert failed is False
    (entry,) = json.loads(text)["items"]
    assert entry["target_registry_name"] == "model-under-test"
    assert entry["identifier"] == {
        "class_name": "OpenAIResponseTarget",
        "endpoint": "https://example.invalid",
        "model_name": "gpt-4",
        "underlying_model_name": "",
        "hash": target.get_identifier().hash,
    }


async def test_listed_scorer_keeps_the_hash_that_pins_it_in_a_run_request(patch_central_database: MagicMock) -> None:
    get_scorer_service.cache_clear()
    ScorerRegistry.reset_registry_singleton()
    scorer = SubStringScorer(substring="flag", categories=["test"])
    ScorerRegistry.get_registry_singleton().instances.register(scorer, name="contains-flag")
    try:
        text, failed = await RestToolset(app=backend_app).call_async(
            name="list_scorers",
            arguments={RestToolset.RESPONSE_FIELDS: ["items.scorer_registry_name", "items.identifier"]},
            authorization=None,
        )
    finally:
        ScorerRegistry.reset_registry_singleton()
        get_scorer_service.cache_clear()

    assert failed is False
    (entry,) = json.loads(text)["items"]
    assert entry["scorer_registry_name"] == "contains-flag"
    # A run request pins its objective scorer as {registry_name, identifier_hash}; eval_hash is a different value.
    assert entry["identifier"]["hash"] == scorer.get_identifier().hash
    assert "eval_hash" not in entry["identifier"]
