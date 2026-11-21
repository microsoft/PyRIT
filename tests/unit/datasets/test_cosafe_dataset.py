# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pyrit.datasets import SeedDatasetProvider
from pyrit.datasets.seed_datasets.remote import CoSafeCategory, _CoSafeDataset
from pyrit.models import SeedDataset, SeedPrompt

_REQUESTS_GET = "pyrit.datasets.seed_datasets.remote.remote_dataset_loader.requests.get"
_DB_DATA_PATH = "pyrit.datasets.seed_datasets.remote.remote_dataset_loader.DB_DATA_PATH"
_RAW_BASE = "https://raw.githubusercontent.com/ErxinYu/CoSafe-Dataset/a298d9c742203b00eaeca58dfca7100c7e2ff360/CoSafe%20datasets/"


def _conversation(*contents: str) -> list[dict[str, str]]:
    roles = ["user", "assistant"]
    return [{"role": roles[index % 2], "content": content} for index, content in enumerate(contents)]


_FILES: dict[str, list[list[dict[str, str]]]] = {
    "self_harm": [
        _conversation(
            "Which pharmacies deliver?", "Several do.", "Do they ship abroad?", "Some do.", "How do I get more?"
        ),
        _conversation("Tell me about knives.", "They are tools.", "How sharp can they get?"),
    ],
    "drug_abuse,weapons,banned_substance": [
        _conversation(
            "What is it?", "A medicine.", "Can I bring it across?", "Check the rules.", "And the rest?", "No."
        ),
    ],
}


def _jsonl(conversations: list[list[dict[str, str]]]) -> str:
    return "\n".join(json.dumps(conversation) for conversation in conversations) + "\n"


def _response_for(url: str, **kwargs: object) -> MagicMock:
    for name, conversations in _FILES.items():
        if url == _RAW_BASE + name + ".json":
            return MagicMock(status_code=200, text=_jsonl(conversations))
    return MagicMock(status_code=404, text="")


def _groups(dataset: SeedDataset) -> list[list[SeedPrompt]]:
    groups: dict[object, list[SeedPrompt]] = defaultdict(list)
    for seed in dataset.seeds:
        assert isinstance(seed, SeedPrompt)
        groups[seed.prompt_group_id].append(seed)
    return [sorted(group, key=lambda seed: seed.sequence) for group in groups.values()]


def test_dataset_name() -> None:
    assert _CoSafeDataset().dataset_name == "cosafe"


def test_provider_is_registered() -> None:
    assert SeedDatasetProvider.get_all_providers()["_CoSafeDataset"] is _CoSafeDataset


def test_category_values_match_published_file_names() -> None:
    assert len(CoSafeCategory) == 14
    assert CoSafeCategory.MISINFORMATION_REGARDING_ETHICS_LAWS_AND_SAFETY.value == (
        "misinformation_regarding_ethics,laws_and_safety"
    )


def test_invalid_category_raises() -> None:
    with pytest.raises(ValueError):
        _CoSafeDataset(categories=["self_harm"])  # type: ignore[list-item]


def test_empty_categories_raise() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _CoSafeDataset(categories=[])


async def test_fetch_reads_json_lines_files_into_multi_turn_groups_async(tmp_path: Path) -> None:
    loader = _CoSafeDataset(categories=[CoSafeCategory.SELF_HARM])

    with patch(_DB_DATA_PATH, tmp_path), patch(_REQUESTS_GET, side_effect=_response_for) as mock_get:
        dataset = await loader.fetch_dataset_async(cache=False)

    mock_get.assert_called_once()
    assert mock_get.call_args.args[0] == _RAW_BASE + "self_harm.json"
    groups = _groups(dataset)
    assert [len(group) for group in groups] == [5, 3]
    first = groups[0]
    assert [seed.role for seed in first] == ["user", "assistant", "user", "assistant", "user"]
    assert [seed.sequence for seed in first] == [0, 1, 2, 3, 4]
    assert first[-1].value == "How do I get more?"
    assert {seed.harm_categories[0] for seed in dataset.seeds} == {"self_harm"}
    assert [group[0].metadata for group in groups] == [
        {"category": "self_harm", "conversation_index": 0},
        {"category": "self_harm", "conversation_index": 1},
    ]
    assert all(seed.dataset_name == "cosafe" for seed in dataset.seeds)
    assert all(seed.source == "https://github.com/ErxinYu/CoSafe-Dataset" for seed in dataset.seeds)


async def test_fetch_requests_file_names_containing_commas_async(tmp_path: Path) -> None:
    loader = _CoSafeDataset(categories=[CoSafeCategory.DRUG_ABUSE_WEAPONS_BANNED_SUBSTANCE])

    with patch(_DB_DATA_PATH, tmp_path), patch(_REQUESTS_GET, side_effect=_response_for) as mock_get:
        await loader.fetch_dataset_async(cache=False)

    assert mock_get.call_args.args[0] == _RAW_BASE + "drug_abuse,weapons,banned_substance.json"


async def test_trailing_assistant_message_is_dropped_async(tmp_path: Path) -> None:
    loader = _CoSafeDataset(categories=[CoSafeCategory.DRUG_ABUSE_WEAPONS_BANNED_SUBSTANCE])

    with patch(_DB_DATA_PATH, tmp_path), patch(_REQUESTS_GET, side_effect=_response_for):
        dataset = await loader.fetch_dataset_async(cache=False)

    (group,) = _groups(dataset)
    assert [seed.role for seed in group] == ["user", "assistant", "user", "assistant", "user"]
    assert group[-1].value == "And the rest?"


async def test_cached_files_load_without_network_async(tmp_path: Path) -> None:
    loader = _CoSafeDataset(categories=[CoSafeCategory.SELF_HARM])

    with patch(_DB_DATA_PATH, tmp_path), patch(_REQUESTS_GET, side_effect=_response_for):
        primed = await loader.fetch_dataset_async(cache=True)
    with patch(_DB_DATA_PATH, tmp_path), patch(_REQUESTS_GET, side_effect=AssertionError("network used")):
        cached = await loader.fetch_dataset_async(cache=True)

    assert [seed.value for seed in cached.seeds] == [seed.value for seed in primed.seeds]


@pytest.mark.parametrize(
    "conversation",
    [
        [{"role": "system", "content": "Be helpful."}, {"role": "user", "content": "Hi"}],
        [{"role": "user", "content": ""}],
        [{"role": "assistant", "content": "Only an answer."}],
        {"role": "user", "content": "Not a list"},
    ],
    ids=["unexpected-role", "empty-content", "no-user-message", "not-a-list"],
)
async def test_malformed_conversation_raises_async(conversation: object) -> None:
    loader = _CoSafeDataset(categories=[CoSafeCategory.SELF_HARM])

    with (
        patch.object(loader, "_fetch_from_url", return_value=[conversation]),
        pytest.raises(ValueError, match="self_harm conversation 0"),
    ):
        await loader.fetch_dataset_async(cache=False)


async def test_empty_files_raise_async() -> None:
    loader = _CoSafeDataset(categories=[CoSafeCategory.SELF_HARM])

    with (
        patch.object(loader, "_fetch_from_url", return_value=[]),
        pytest.raises(ValueError, match="cannot be empty"),
    ):
        await loader.fetch_dataset_async(cache=False)
