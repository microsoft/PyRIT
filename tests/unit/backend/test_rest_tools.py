# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel
from starlette.applications import Starlette

from pyrit import _compatibility
from pyrit.backend.main import app as backend_app
from pyrit.backend.rest_tools import RestToolset
from pyrit.backend.routes import scenarios as scenario_routes
from pyrit.backend.services import ScenarioService
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
# Values that the backend's own IDs and names look like.
BACKEND_PATH_VALUES = {
    "scenario_name": "technique.execute",
    "scenario_result_id": "4b9f3a52-6c1e-4d8a-9f2b-7e0c5d1a3b68",
    "attack_result_id": "c2d7e8f1-0a3b-4c5d-8e9f-1a2b3c4d5e6f",
}
INVALID_THING_ID = "Invalid path parameters: thing_id. Pass IDs and names exactly as a tool returned them."
INVALID_FILE_PATH = "Invalid path parameters: file_path. Pass IDs and names exactly as a tool returned them."


class ThingRequest(BaseModel):
    name: str


@pytest.fixture
def toolset() -> Iterator[RestToolset]:
    app = FastAPI()
    app.state.compatibility_id = COMPATIBILITY_ID

    @app.get("/api/things/featured")
    async def get_featured_thing() -> dict[str, object]:
        """Get the featured thing, which is not offered and is matched before any thing ID."""
        return {"featured": True}

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

    @app.post("/api/things")
    async def create_thing(thing: ThingRequest) -> dict[str, object]:
        """Create a thing."""
        return {"created": thing.model_dump()}

    files = APIRouter(prefix="/files")

    @files.get("/{file_path:path}/size")
    async def get_file_size(file_path: str) -> dict[str, object]:
        """Get a file's size, which is not offered and is matched before the file itself."""
        return {"size": 1}

    @files.get("/{file_path:path}")
    async def get_file(file_path: str) -> dict[str, object]:
        """Get one file by its path."""
        return {"path": file_path}

    app.include_router(files, prefix="/api")
    # Like the bundled frontend, a mount registered last that takes every path no route matched.
    app.mount("/", Starlette())

    offered = ("get_thing", "list_things", "get_notes", "get_conversation", "create_thing", "get_file")
    with patch.object(RestToolset, "OPERATIONS", offered):
        yield RestToolset(app=app)


def test_tools_are_rest_operations_named_after_their_route_functions(toolset: RestToolset) -> None:
    operations = {operation.name: operation for operation in toolset.operations}

    assert list(operations) == [
        "get_thing",
        "list_things",
        "get_notes",
        "get_conversation",
        "create_thing",
        "get_file",
    ]
    assert operations["get_thing"].description.startswith("GET /api/things/{thing_id}\n\nGet one thing.")
    assert operations["get_thing"].input_schema["required"] == ["thing_id"]
    assert set(operations["list_things"].input_schema["properties"]) == {"limit", RestToolset.RESPONSE_FIELDS}
    assert operations["create_thing"].input_schema["required"] == ["body"]


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


async def test_call_sends_the_body(toolset: RestToolset) -> None:
    text, failed = await toolset.call_async(name="create_thing", arguments={"body": {"name": "n"}}, authorization=None)

    assert failed is False
    assert json.loads(text) == {"created": {"name": "n"}}


@pytest.mark.parametrize(
    ("name", "arguments", "error"),
    [
        ("no_such_tool", {}, "Unknown tool 'no_such_tool'."),
        ("get_thing", {"thing_id": None}, "Missing path parameters: thing_id."),
        # Each would reach another route or none: more segments, a static route registered first, the list
        # route ("."), the API root (".."), a path with a trailing slash, and both of two path routes.
        ("get_thing", {"thing_id": "a/secret"}, INVALID_THING_ID),
        ("get_thing", {"thing_id": "featured"}, INVALID_THING_ID),
        ("get_thing", {"thing_id": "."}, INVALID_THING_ID),
        ("get_thing", {"thing_id": ".."}, INVALID_THING_ID),
        ("get_thing", {"thing_id": ""}, INVALID_THING_ID),
        ("get_file", {"file_path": "docs/size"}, INVALID_FILE_PATH),
    ],
)
async def test_call_reports_unknown_tools_and_invalid_path_parameters(
    toolset: RestToolset, name: str, arguments: dict[str, object], error: str
) -> None:
    text, failed = await toolset.call_async(name=name, arguments=arguments, authorization=None)

    assert failed is True
    assert json.loads(text) == {"error": error}


async def test_path_converter_parameter_keeps_its_slashes(toolset: RestToolset) -> None:
    text, failed = await toolset.call_async(
        name="get_file", arguments={"file_path": "docs/guides/setup.md"}, authorization=None
    )

    assert failed is False
    assert json.loads(text) == {"path": "docs/guides/setup.md"}


async def test_response_over_the_limit_is_a_note_that_names_its_fields(toolset: RestToolset) -> None:
    with patch.object(RestToolset, "MAX_RESPONSE_CHARS", 1_000):
        text, failed = await toolset.call_async(name="list_things", arguments={"limit": 100}, authorization=None)

    page = {
        "items": [{"name": f"thing-{index}", "text": "x" * 50} for index in range(100)],
        "pagination": {"limit": 100, "has_more": True, "next_cursor": "after-the-page"},
    }
    length = len(json.dumps(page, separators=(",", ":")))
    # No partial page: its continuation cursor would skip whatever was left out.
    assert failed is False
    assert json.loads(text) == {
        "note": f"The response has {length:,} characters, over the 1,000 limit. Request fewer at a time, such as "
        "with a smaller limit, or pass response_fields to keep only what you need.",
        "available_fields": ["items", "pagination"],
    }


@pytest.mark.parametrize("cursor_field", ["next_cursor", "next_group_offset"])
async def test_response_that_fits_keeps_its_continuation_field(toolset: RestToolset, cursor_field: str) -> None:
    with patch.object(RestToolset, "MAX_RESPONSE_CHARS", 1_000):
        text, _ = await toolset.call_async(
            name="get_notes", arguments={"count": 3, "cursor_field": cursor_field}, authorization=None
        )

    assert json.loads(text) == {
        "notebook": "n",
        "messages": [{"text": "m" * 10}] * 3,
        cursor_field: "after-the-page",
    }


async def test_long_text_is_a_preview_unless_its_field_is_requested(toolset: RestToolset) -> None:
    preview, _ = await toolset.call_async(name="get_notes", arguments={"length": 1_500}, authorization=None)
    full, _ = await toolset.call_async(
        name="get_notes",
        arguments={"length": 1_500, RestToolset.RESPONSE_FIELDS: ["messages.text"]},
        authorization=None,
    )

    assert json.loads(preview)["messages"] == [
        {"text": "m" * 1_000 + "… [shortened from 1,500 characters; the full text is in the PyRIT GUI or REST API]"}
    ]
    assert json.loads(full) == {"messages": [{"text": "m" * 1_500}]}


async def test_named_text_too_long_for_any_response_is_a_marked_preview(toolset: RestToolset) -> None:
    text, failed = await toolset.call_async(
        name="get_conversation", arguments={RestToolset.RESPONSE_FIELDS: ["messages.text"]}, authorization=None
    )

    # No page could hold the prompt, so it is shortened and the reply after it stays readable.
    assert failed is False
    assert json.loads(text) == {
        "messages": [
            {
                "text": "q" * 1_000
                + "… [shortened from 50,000 characters; the full text is in the PyRIT GUI or REST API]"
            },
            {"text": "Sure, here is the answer."},
        ]
    }


async def test_named_text_over_the_limit_is_a_note_instead_of_previews(toolset: RestToolset) -> None:
    text, _ = await toolset.call_async(
        name="get_notes",
        arguments={"count": 30, "length": 2_000, RestToolset.RESPONSE_FIELDS: ["messages.text"]},
        authorization=None,
    )

    length = len(json.dumps({"messages": [{"text": "m" * 2_000}] * 30}, separators=(",", ":")))
    assert json.loads(text) == {
        "note": f"The response has {length:,} characters, over the 40,000 limit. Request fewer at a time, such as "
        "with a smaller limit, or pass response_fields to keep only what you need.",
        "available_fields": ["messages"],
    }


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


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_attack", {"attack_result_id": "attack-options"}),
        ("get_attack", {"attack_result_id": "converter-options"}),
        ("get_attack", {"attack_result_id": "a/b"}),
        ("get_scenario", {"scenario_name": "team/custom-scan/datasets"}),
        ("get_scenario_run", {"scenario_result_id": "queue"}),
        ("get_conversation_messages", {"attack_result_id": "conversations", "conversation_id": "c"}),
    ],
)
async def test_backend_tool_does_not_send_a_value_that_reaches_another_route(
    name: str, arguments: dict[str, object]
) -> None:
    # Each value reaches another route, such as GET /api/scenarios/runs/queue registered before the tool's own,
    # or none.
    toolset = RestToolset(app=backend_app)
    with patch.object(toolset, "_send_async", new_callable=AsyncMock) as send:
        text, failed = await toolset.call_async(name=name, arguments=arguments, authorization=None)

    assert failed is True
    assert json.loads(text)["error"].startswith("Invalid path parameters")
    send.assert_not_called()


async def test_backend_tools_send_ids_and_names_to_their_own_routes() -> None:
    toolset = RestToolset(app=backend_app)
    rejected = []
    with patch.object(toolset, "_send_async", new_callable=AsyncMock, return_value=httpx.Response(200, json={})):
        for operation in toolset.operations:
            arguments = {parameter: BACKEND_PATH_VALUES[parameter] for parameter in operation.path_parameters}
            _, failed = await toolset.call_async(name=operation.name, arguments=arguments, authorization=None)
            if failed:
                rejected.append(operation.name)

    assert rejected == []


async def test_backend_get_scenario_receives_a_name_with_a_slash() -> None:
    service = MagicMock(spec=ScenarioService)
    service.get_scenario_async = AsyncMock(return_value=None)
    with patch.object(scenario_routes, "get_scenario_service", return_value=service):
        text, failed = await RestToolset(app=backend_app).call_async(
            name="get_scenario", arguments={"scenario_name": "team/custom-scan"}, authorization=None
        )

    assert service.get_scenario_async.call_args.kwargs == {"scenario_name": "team/custom-scan"}
    assert failed is True
    assert json.loads(text)["error"]["detail"] == "Scenario 'team/custom-scan' not found"


def _references(node: object) -> list[str]:
    if isinstance(node, dict):
        reference = node.get("$ref")
        own: list[str] = [reference] if isinstance(reference, str) else []
        return own + [found for value in node.values() for found in _references(value)]
    if isinstance(node, list):
        return [found for value in node for found in _references(value)]
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
