# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib
import zipfile
from unittest.mock import AsyncMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.jailbreakv_28k_dataset import (
    _HarmCategory,
    _JailbreakV28KDataset,
)
from pyrit.models import SeedDataset, SeedObjective, SeedPrompt


def _row(
    *,
    policy: str = "Hate Speech",
    redteam_query: str = "underlying goal",
    jailbreak_query: str = "jailbreak text",
    image_path: str = "llm_transfer_attack/img_001.png",
    fmt: str = "Image+Text",
    transfer_attack_type: str = "SD",
    row_id: int = 1,
) -> dict:
    return {
        "id": row_id,
        "policy": policy,
        "redteam_query": redteam_query,
        "jailbreak_query": jailbreak_query,
        "image_path": image_path,
        "format": fmt,
        "transfer_attack_type": transfer_attack_type,
    }


def _setup_zip_dir(tmp_path: pathlib.Path, image_rel_paths: list[str]) -> pathlib.Path:
    """Create a fake zip + already-extracted folder with images under tmp_path."""
    (tmp_path / "JailBreakV_28K.zip").write_bytes(b"fake")
    extracted = tmp_path / "JailBreakV_28k"
    extracted.mkdir(exist_ok=True)
    for rel in image_rel_paths:
        img_path = extracted / rel
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(b"\x89PNG")
    return tmp_path


def test_dataset_name():
    assert _JailbreakV28KDataset().dataset_name == "jailbreakv_28k"


def test_class_level_metadata_present():
    assert _JailbreakV28KDataset.HF_DATASET_NAME == "JailbreakV-28K/JailBreakV-28k"
    assert _JailbreakV28KDataset.modalities == ["text", "image"]
    assert _JailbreakV28KDataset.size == "medium"
    assert "jailbreak" in _JailbreakV28KDataset.tags


def test_init_rejects_raw_string_harm_category():
    with pytest.raises(ValueError, match="Expected _HarmCategory"):
        _JailbreakV28KDataset(harm_categories=["Hate Speech"])  # type: ignore[list-item]


def test_init_empty_harm_categories_raises():
    with pytest.raises(ValueError, match="`harm_categories` must be a non-empty list"):
        _JailbreakV28KDataset(harm_categories=[])


def test_init_accepts_valid_enum():
    loader = _JailbreakV28KDataset(harm_categories=[_HarmCategory.HATE_SPEECH])
    assert loader.filter_categories == [_HarmCategory.HATE_SPEECH]


def test_fetch_dataset_missing_zip_raises(tmp_path):
    loader = _JailbreakV28KDataset(zip_dir=str(tmp_path))
    # No zip file present in tmp_path → FileNotFoundError
    import asyncio

    with pytest.raises(FileNotFoundError, match="ZIP file not found"):
        asyncio.run(loader.fetch_dataset_async())


async def test_fetch_dataset_happy_path(tmp_path):
    image_rel = "llm_transfer_attack/img_001.png"
    _setup_zip_dir(tmp_path, [image_rel])
    loader = _JailbreakV28KDataset(zip_dir=str(tmp_path))
    rows = [_row(image_path=image_rel)]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        dataset = await loader.fetch_dataset_async()

    assert isinstance(dataset, SeedDataset)
    assert len(dataset.seeds) == 3  # 1 row × 3 seeds (objective + text + image)
    objective, text, image = dataset.seeds
    assert isinstance(objective, SeedObjective)
    assert isinstance(text, SeedPrompt) and text.data_type == "text"
    assert isinstance(image, SeedPrompt) and image.data_type == "image_path"
    # Group ID is shared across all three
    assert objective.prompt_group_id == text.prompt_group_id == image.prompt_group_id
    # Source casing preserved on the seed
    assert objective.harm_categories == ["Hate Speech"]
    assert text.harm_categories == ["Hate Speech"]
    # Per-row trace-back metadata reaches all three
    for seed in dataset.seeds:
        assert seed.metadata["source_format"] == "Image+Text"
        assert seed.metadata["transfer_attack_type"] == "SD"
        assert seed.metadata["policy"] == "Hate Speech"
        assert seed.metadata["row_id"] == "1"


async def test_fetch_dataset_filters_by_harm_category(tmp_path):
    image_rel = "img.png"
    _setup_zip_dir(tmp_path, [image_rel])
    loader = _JailbreakV28KDataset(
        zip_dir=str(tmp_path),
        harm_categories=[_HarmCategory.HATE_SPEECH],
    )
    rows = [
        _row(policy="Hate Speech", image_path=image_rel),
        _row(policy="Violence", image_path=image_rel, row_id=2),
    ]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        dataset = await loader.fetch_dataset_async()

    # Only Hate Speech row passes
    assert len(dataset.seeds) == 3
    assert all(s.harm_categories == ["Hate Speech"] for s in dataset.seeds)


async def test_fetch_dataset_empty_after_filter_raises(tmp_path):
    _setup_zip_dir(tmp_path, [])
    loader = _JailbreakV28KDataset(
        zip_dir=str(tmp_path),
        harm_categories=[_HarmCategory.CHILD_ABUSE_CONTENT],
    )
    rows = [_row(policy="Hate Speech")]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
            await loader.fetch_dataset_async()


async def test_fetch_dataset_too_many_missing_images_raises(tmp_path):
    _setup_zip_dir(tmp_path, ["found.png"])
    loader = _JailbreakV28KDataset(zip_dir=str(tmp_path))
    # 1 resolves, 3 don't → 75% missing, exceeds 50% threshold
    rows = [
        _row(image_path="found.png", row_id=0),
        _row(image_path="missing1.png", row_id=1),
        _row(image_path="missing2.png", row_id=2),
        _row(image_path="missing3.png", row_id=3),
    ]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        with pytest.raises(ValueError, match="missing images"):
            await loader.fetch_dataset_async()


async def test_fetch_dataset_some_missing_images_warns_but_succeeds(tmp_path):
    """1 missing out of 3 = 33% missing, under threshold → log warning, return successful seeds."""
    _setup_zip_dir(tmp_path, ["a.png", "b.png"])
    loader = _JailbreakV28KDataset(zip_dir=str(tmp_path))
    rows = [
        _row(image_path="a.png", row_id=0),
        _row(image_path="b.png", row_id=1),
        _row(image_path="missing.png", row_id=2),
    ]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        dataset = await loader.fetch_dataset_async()

    # 2 successful groups × 3 seeds
    assert len(dataset.seeds) == 6


async def test_fetch_dataset_logs_and_reraises_on_hf_error(tmp_path):
    _setup_zip_dir(tmp_path, [])
    loader = _JailbreakV28KDataset(zip_dir=str(tmp_path))

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(side_effect=RuntimeError("hf down"))):
        with pytest.raises(RuntimeError, match="hf down"):
            await loader.fetch_dataset_async()


def test_normalize_policy_lowercases_and_underscores():
    loader = _JailbreakV28KDataset()
    assert loader._normalize_policy("Hate Speech") == "hate_speech"
    assert loader._normalize_policy("Tailored-Unlicensed Advice") == "tailored_unlicensed_advice"
    assert loader._normalize_policy("  Padded  ") == "padded"


def test_resolve_image_path_caches_results(tmp_path):
    loader = _JailbreakV28KDataset()
    existing = tmp_path / "exists.png"
    existing.write_bytes(b"\x89PNG")
    cache: dict[str, str] = {}

    first = loader._resolve_image_path(rel_path="exists.png", local_directory=tmp_path, call_cache=cache)
    second = loader._resolve_image_path(rel_path="exists.png", local_directory=tmp_path, call_cache=cache)
    missing = loader._resolve_image_path(rel_path="missing.png", local_directory=tmp_path, call_cache=cache)
    empty = loader._resolve_image_path(rel_path="", local_directory=tmp_path, call_cache=cache)

    assert first == str(existing)
    assert second == first  # cache hit
    assert missing == ""
    assert empty == ""
    assert cache == {"exists.png": str(existing), "missing.png": ""}


def test_resolve_image_path_returns_empty_on_exists_exception(tmp_path):
    """If Path.exists() raises (e.g. OSError on overlong paths), fall back to ''."""
    loader = _JailbreakV28KDataset()
    cache: dict[str, str] = {}

    with patch("pathlib.Path.exists", side_effect=OSError("simulated stat failure")):
        result = loader._resolve_image_path(rel_path="anything.png", local_directory=tmp_path, call_cache=cache)

    assert result == ""
    assert cache == {"anything.png": ""}


async def test_fetch_dataset_skips_rows_with_empty_image_path(tmp_path):
    """Rows missing the image_path field count as unpaired and don't create seeds."""
    _setup_zip_dir(tmp_path, ["a.png", "b.png"])
    loader = _JailbreakV28KDataset(zip_dir=str(tmp_path))
    rows = [
        _row(image_path="a.png", row_id=0),
        _row(image_path="b.png", row_id=1),
        _row(image_path="", row_id=2),  # empty image_path -> counted missing, skipped
    ]

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        dataset = await loader.fetch_dataset_async()

    # 2 successful groups * 3 seeds; the empty-image row is dropped
    assert len(dataset.seeds) == 6


async def test_fetch_dataset_extracts_zip_when_target_missing(tmp_path):
    """When the unzipped directory doesn't exist, the loader extracts the zip."""
    image_rel = "llm_transfer_attack/img_001.png"

    # Build a real zip containing the expected extracted layout (JailBreakV_28k/<rel>)
    zip_path = tmp_path / "JailBreakV_28K.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"JailBreakV_28k/{image_rel}", b"\x89PNG")

    loader = _JailbreakV28KDataset(zip_dir=str(tmp_path))
    rows = [_row(image_path=image_rel)]

    extracted = tmp_path / "JailBreakV_28k"
    assert not extracted.exists()  # precondition: extract branch will fire

    with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
        dataset = await loader.fetch_dataset_async()

    assert extracted.exists() and (extracted / image_rel).exists()
    assert len(dataset.seeds) == 3


async def test_fetch_dataset_extract_raises_total_size_cap(tmp_path):
    """The full JailBreakV_28K.zip (~15 GiB, ratio ~1x) exceeds safe_extract_zip's 5 GiB
    default total-size cap, so the loader must forward a raised max_total_size."""
    image_rel = "llm_transfer_attack/img_001.png"
    (tmp_path / "JailBreakV_28K.zip").write_bytes(b"fake")
    loader = _JailbreakV28KDataset(zip_dir=str(tmp_path))
    rows = [_row(image_path=image_rel)]

    def _fake_extract(*, source, dest_dir, max_total_size, **kwargs):
        target = pathlib.Path(dest_dir) / "JailBreakV_28k" / image_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG")

    with patch(
        "pyrit.datasets.seed_datasets.remote.jailbreakv_28k_dataset.safe_extract_zip",
        side_effect=_fake_extract,
    ) as mock_extract:
        with patch.object(loader, "_fetch_from_huggingface_async", new=AsyncMock(return_value=rows)):
            await loader.fetch_dataset_async()

    forwarded = mock_extract.call_args.kwargs["max_total_size"]
    assert forwarded == _JailbreakV28KDataset._EXTRACT_MAX_TOTAL_SIZE
    assert forwarded > 15 * 1024**3  # accommodates the real ~15 GiB bundle
