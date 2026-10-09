# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from pyrit.backend.main import app
from pyrit.backend.services.dataset_service import get_dataset_service
from pyrit.common.utils import to_sha256
from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import SeedEntry
from pyrit.models import (
    AnswerMatches,
    MatchesObjective,
    PromptDataType,
    Seed,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)

URL = "/api/datasets/seeds"
DATASET = "browse"
NAMED = f"dataset:named:{DATASET}"
MISSING_ID = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
async def client(patch_central_database, compatibility_headers: dict[str, str]) -> AsyncIterator[AsyncClient]:
    get_dataset_service.cache_clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=compatibility_headers) as client:
        yield client
    get_dataset_service.cache_clear()


async def _store(memory: MemoryInterface, *seeds: Seed) -> None:
    async with await memory.get_session_async() as session:
        session.add_all(SeedEntry(entry=seed) for seed in seeds)
        await session.commit()


def _prompt(value: str, **kwargs) -> SeedPrompt:
    return SeedPrompt(value=value, dataset_name=DATASET, added_by="test", **kwargs)


def _conversation(**kwargs) -> SeedSimulatedConversation:
    return SeedSimulatedConversation(
        num_turns=2,
        adversarial_chat_system_prompt=SeedPrompt(value="adversarial", parameters=["objective"]),
        simulated_target_system_prompt=SeedPrompt(value="target", parameters=["objective", "num_turns"]),
        dataset_name=DATASET,
        added_by="test",
        **kwargs,
    )


async def test_list_seed_examples_pages_with_a_filter_bound_cursor(client: AsyncClient, sqlite_instance):
    await _store(sqlite_instance, *(_prompt(f"prompt {index}") for index in range(3)), _prompt("other"))
    params = {"selection_key": NAMED, "limit": 2, "search": "PROMPT"}

    first = (await client.get(URL, params=params)).json()
    cursor = first["pagination"]["next_cursor"]
    second = (await client.get(URL, params={**params, "cursor": cursor})).json()
    mismatched = await client.get(URL, params={"selection_key": NAMED, "limit": 2, "cursor": cursor})

    assert len({item["example_id"] for item in first["items"] + second["items"]}) == 3
    assert first["total"] == second["total"] == 3
    assert (first["pagination"]["has_more"], second["pagination"]["has_more"]) == (True, False)
    assert second["pagination"]["prev_cursor"] == cursor
    assert mismatched.status_code == 400


@pytest.mark.parametrize(
    ("path", "params", "expected_status"),
    [
        ("", {"selection_key": NAMED, "cursor": "not-a-cursor"}, 400),
        ("", {"selection_key": DATASET}, 400),
        ("", {"selection_key": "dataset:named:"}, 400),
        ("", {"selection_key": NAMED, "limit": 0}, 422),
        (f"/{MISSING_ID}", {"selection_key": "dataset:other"}, 400),
        (f"/{MISSING_ID}", {"selection_key": NAMED}, 404),
        ("/not-a-uuid", {"selection_key": NAMED}, 422),
    ],
)
async def test_seed_example_routes_reject_bad_requests(
    client: AsyncClient, path: str, params: dict[str, str | int], expected_status: int
):
    response = await client.get(URL + path, params=params)

    assert response.status_code == expected_status


async def test_list_seed_examples_builds_safe_previews(client: AsyncClient, sqlite_instance):
    long_text = _prompt("x" * 150)
    image = _prompt("https://account.blob.core.windows.net/c/cat.png?sig=secret", data_type="image_path")
    url = _prompt("https://example.com/?token=secret", data_type="url")
    configuration = _conversation()
    await _store(sqlite_instance, long_text, image, url, configuration)

    response = await client.get(URL, params={"selection_key": NAMED})

    items = {item["example_id"]: item for item in response.json()["items"]}
    assert (items[str(long_text.id)]["preview"], items[str(long_text.id)]["preview_truncated"]) == (
        "x" * 100 + "...",
        True,
    )
    assert items[str(image.id)]["preview"] == "[Image: cat.png]"
    assert items[str(url.id)]["preview"] == "[url]"
    assert items[str(configuration.id)]["preview"] == "[Simulated conversation configuration]"


@pytest.mark.parametrize(
    ("data_type", "label"),
    [("image_path", "Image"), ("audio_path", "Audio"), ("video_path", "Video"), ("binary_path", "File")],
)
@pytest.mark.parametrize("prefix", ["HTTPS://", "hTtP://", " \thttps://"])
async def test_seed_example_media_preview_normalizes_url_prefix_async(
    *,
    client: AsyncClient,
    sqlite_instance: MemoryInterface,
    data_type: PromptDataType,
    label: str,
    prefix: str,
) -> None:
    url = f"{prefix}reader:password@example.test/private/file.png?sig=secret#token=secret"
    seed = _prompt(url, data_type=data_type)
    await _store(sqlite_instance, seed)

    listed = await client.get(URL, params={"selection_key": NAMED})
    detail = await client.get(f"{URL}/{seed.id}", params={"selection_key": NAMED})

    assert listed.status_code == detail.status_code == 200
    assert listed.json()["items"][0]["preview"] == f"[{label}: file.png]"
    assert listed.json()["items"][0]["preview_truncated"] is False
    assert detail.json()["preview"] == f"[{label}: file.png]"
    assert detail.json()["members"][0]["value"] == url


@pytest.mark.parametrize(
    "value",
    [
        "/private/seed.txt",
        r"C:\private\seed.txt",
        r"\\server\private\seed.txt",
        "https://storage.example.test/seed.txt?sig=secret",
        "HTTPS://user:password@example.test/seed.txt?token=secret",
        " \thttps://example.test/seed.txt?token=secret",
    ],
)
async def test_seed_example_preview_hides_text_references_async(
    client: AsyncClient, sqlite_instance: MemoryInterface, value: str
) -> None:
    seed = _prompt(value, data_type="text")
    await _store(sqlite_instance, seed)

    listed = await client.get(URL, params={"selection_key": NAMED})
    detail = await client.get(f"{URL}/{seed.id}", params={"selection_key": NAMED})

    assert listed.status_code == detail.status_code == 200
    item = listed.json()["items"][0]
    assert item["preview"] == "[Text reference]"
    assert item["preview_truncated"] is False
    assert detail.json()["members"][0]["value"] == value


@pytest.mark.parametrize("value", ["User: describe the image", "Discuss /private/example without opening it"])
async def test_seed_example_preview_preserves_ordinary_prose_async(
    client: AsyncClient, sqlite_instance: MemoryInterface, value: str
) -> None:
    await _store(sqlite_instance, _prompt(value))

    response = await client.get(URL, params={"selection_key": NAMED})

    assert response.status_code == 200
    assert response.json()["items"][0]["preview"] == value


async def test_get_seed_example_returns_full_text_of_truncated_preview(client: AsyncClient, sqlite_instance):
    seed = _prompt("x" * 150)
    await _store(sqlite_instance, seed)

    listed = (await client.get(URL, params={"selection_key": NAMED})).json()
    detail = (await client.get(f"{URL}/{seed.id}", params={"selection_key": NAMED})).json()

    assert listed["items"][0]["preview_truncated"] is True
    assert detail["preview_truncated"] is True
    assert detail["members"][0]["value"] == "x" * 150


async def test_get_seed_example_returns_objective_conditions(client: AsyncClient, sqlite_instance):
    objective = SeedObjective(
        value="question",
        dataset_name=DATASET,
        added_by="test",
        conditions=(AnswerMatches(correct_answer="Paris", correct_answer_label="A"), MatchesObjective()),
    )
    await _store(sqlite_instance, objective)

    response = await client.get(f"{URL}/{objective.id}", params={"selection_key": NAMED})

    conditions = response.json()["members"][0]["conditions"]
    assert [condition["condition_type"] for condition in conditions] == ["answer_matches", "matches_objective"]
    assert conditions[0]["correct_answer"] == "Paris"
    assert conditions[0]["correct_answer_label"] == "A"


@pytest.mark.parametrize(
    "params",
    [{"selection_key": "dataset:named:empty"}, {"selection_key": NAMED, "search": "no match"}],
)
async def test_list_seed_examples_returns_empty_page(client: AsyncClient, sqlite_instance, params: dict[str, str]):
    await _store(sqlite_instance, _prompt("prompt"))

    response = await client.get(URL, params=params)

    assert response.status_code == 200
    body = response.json()
    assert (body["items"], body["total"]) == ([], 0)
    assert (body["pagination"]["has_more"], body["pagination"]["next_cursor"]) == (False, None)


async def test_get_seed_example_returns_all_stored_members(client: AsyncClient, sqlite_instance):
    group = uuid4()
    objective = SeedObjective(
        value="objective", dataset_name=DATASET, prompt_group_id=group, harm_categories=["violence"], added_by="test"
    )
    prompt = _prompt("prompt", prompt_group_id=group, metadata={"source_id": 7}, sequence=1)
    configuration = _conversation(prompt_group_id=group)
    await _store(sqlite_instance, prompt, configuration, objective)

    response = await client.get(f"{URL}/{group}", params={"selection_key": NAMED})
    unnamed = await client.get(f"{URL}/{group}", params={"selection_key": "dataset:unnamed"})

    body = response.json()
    members = {member["id"]: member for member in body["members"]}
    assert body["members"][0]["id"] == str(objective.id)
    assert set(members) == {str(objective.id), str(prompt.id), str(configuration.id)}
    assert members[str(prompt.id)]["metadata"] == {"source_id": 7}
    assert members[str(configuration.id)]["seed_type"] == "simulated_conversation"
    assert members[str(configuration.id)]["value"] == configuration.value
    stored_configuration = json.loads(members[str(configuration.id)]["value"])
    assert stored_configuration["num_turns"] == 2
    assert stored_configuration["adversarial_chat_system_prompt"]["value"] == "adversarial"
    assert (body["preview"], body["objective_count"], body["has_unlabeled_harm"]) == ("objective", 1, True)
    assert unnamed.status_code == 404


async def test_seed_example_routes_preserve_legacy_configuration_without_reconstruction_async(
    client: AsyncClient, sqlite_instance: MemoryInterface, tmp_path: Path
) -> None:
    group = uuid4()
    prompt = _prompt("related prompt", prompt_group_id=group)
    configuration = SeedEntry(entry=_conversation(prompt_group_id=group))
    configuration.value = json.dumps(
        {"num_turns": 2, "adversarial_chat_system_prompt_path": str(tmp_path / "missing.yaml")}
    )
    configuration.value_sha256 = to_sha256(configuration.value)
    expected_id, expected_value, expected_hash = configuration.id, configuration.value, configuration.value_sha256
    await _store(sqlite_instance, prompt)
    async with await sqlite_instance.get_session_async() as session:
        session.add(configuration)
        await session.commit()

    with (
        patch.object(SeedEntry, "get_seed", side_effect=AssertionError("seed reconstruction")),
        patch.object(Path, "read_text", side_effect=AssertionError("file read")),
        patch.object(SeedPrompt, "render_template_value_silent", side_effect=AssertionError("template rendering")),
    ):
        listed = await client.get(URL, params={"selection_key": NAMED, "seed_type": "simulated_conversation"})
        detail = await client.get(f"{URL}/{group}", params={"selection_key": NAMED})

    assert listed.status_code == detail.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["piece_count"] == 2
    assert listed.json()["items"][0]["seed_types"] == ["prompt", "simulated_conversation"]
    members = {member["id"]: member for member in detail.json()["members"]}
    assert set(members) == {str(prompt.id), str(expected_id)}
    assert members[str(expected_id)]["value"] == expected_value
    assert members[str(expected_id)]["value_sha256"] == expected_hash
