# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.end_to_end import test_all_datasets as dataset_tests

_LANGUAGE_TEXT = {
    "en": "Please describe the scene in this image.",
    "zh": "\u8bf7\u63cf\u8ff0\u56fe\u7247\u4e2d\u7684\u573a\u666f\u3002",
    "vi": "H\u00e3y m\u00f4 t\u1ea3 c\u1ea3nh trong \u1ea3nh.",
}


def test_variant_module_load_does_not_materialize_provider_registry() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path\n"
                "from runpy import run_path\n"
                "from unittest.mock import patch\n"
                "from pyrit.datasets import SeedDatasetProvider\n"
                "with patch.object(SeedDatasetProvider, 'get_all_providers', "
                "side_effect=AssertionError('registry materialized during import')):\n"
                "    run_path(str(Path('tests') / 'end_to_end' / 'test_all_datasets.py'))\n"
            ),
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fixturenames", [["case_id"], ["name", "provider_cls"]])
def test_provider_registry_is_materialized_only_for_default_sweep(fixturenames: list[str]) -> None:
    metafunc = MagicMock(spec=pytest.Metafunc)
    metafunc.fixturenames = fixturenames
    providers = {"aya": dataset_tests._AyaRedteamingDataset, "catqa": dataset_tests._CategoricalHarmfulQADataset}
    with patch.object(dataset_tests.SeedDatasetProvider, "get_all_providers", return_value=providers) as get_providers:
        dataset_tests.pytest_generate_tests(metafunc)
        if "provider_cls" in fixturenames:
            get_providers.assert_called_once()
            assert metafunc.parametrize.call_args.args == ("name,provider_cls", list(providers.items()))
        else:
            get_providers.assert_not_called()
            metafunc.parametrize.assert_not_called()


@pytest.mark.parametrize("language", ["zh", "vi"])
@pytest.mark.parametrize("matching_rows", [8, 9, 10])
async def test_catqa_language_threshold_async(*, language: str, matching_rows: int) -> None:
    values = [_LANGUAGE_TEXT[language]] * matching_rows + [_LANGUAGE_TEXT["en"]] * (10 - matching_rows)
    rows = [{"Question": value, "Category": "Fraud/Deception", "Subcategory": "Phishing"} for value in values]
    case_id = f"categorical-harmful-qa-{language}"
    with patch.object(
        dataset_tests._CategoricalHarmfulQADataset,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=rows),
    ):
        if matching_rows < 9:
            with pytest.raises(AssertionError, match="match the requested language"):
                await dataset_tests.TestAllDatasets().test_fetch_non_default_variant_async(case_id=case_id)
        else:
            await dataset_tests.TestAllDatasets().test_fetch_non_default_variant_async(case_id=case_id)


@pytest.mark.parametrize(
    ("requested", "served"),
    [("zh", "vi"), ("vi", "zh"), ("zh", "en"), ("vi", "en")],
)
async def test_catqa_rejects_wrong_language_async(*, requested: str, served: str) -> None:
    rows = [
        {
            "Question": f"\u201c{_LANGUAGE_TEXT[served]}\u201d",
            "Category": "Fraud/Deception",
            "Subcategory": "Phishing",
        }
    ]
    with patch.object(
        dataset_tests._CategoricalHarmfulQADataset,
        "_fetch_from_huggingface_async",
        new=AsyncMock(return_value=rows),
    ):
        with pytest.raises(AssertionError, match="match the requested language"):
            await dataset_tests.TestAllDatasets().test_fetch_non_default_variant_async(
                case_id=f"categorical-harmful-qa-{requested}"
            )


@pytest.fixture(
    params=[
        ("vlguard-safe-safes", "safe_instruction"),
        ("vlguard-safe-unsafes", "unsafe_instruction"),
    ],
    ids=["safe-safes", "safe-unsafes"],
)
def vlguard_variant(request: pytest.FixtureRequest) -> tuple[str, str]:
    case_id, instruction_key = request.param
    assert isinstance(case_id, str) and isinstance(instruction_key, str)
    return case_id, instruction_key


@pytest.fixture
def vlguard_records() -> list[dict[str, Any]]:
    return [
        {
            "image": f"image_{index}.png",
            "safe": True,
            "instr-resp": [
                {"safe_instruction": f"Describe scene {index}."},
                {"unsafe_instruction": f"Alternate instruction {index}."},
            ],
        }
        for index in range(2)
    ]


@pytest.fixture
def image_dir(tmp_path: Path) -> Path:
    for index in range(2):
        (tmp_path / f"image_{index}.png").write_bytes(b"fixture image")
    return tmp_path


@pytest.fixture
def mock_vlguard_download(*, vlguard_records: list[dict[str, Any]], image_dir: Path) -> Iterator[AsyncMock]:
    with patch.dict("os.environ", {"HUGGINGFACE_TOKEN": "unused-fixture-token"}):
        with patch.object(
            dataset_tests._VLGuardDataset,
            "_download_dataset_files_async",
            new=AsyncMock(return_value=(vlguard_records, image_dir)),
        ) as download:
            yield download


async def test_vlguard_safe_image_contract_async(
    *, vlguard_variant: tuple[str, str], mock_vlguard_download: AsyncMock
) -> None:
    case_id, _ = vlguard_variant
    await dataset_tests.TestAllDatasets().test_fetch_non_default_variant_async(case_id=case_id)
    assert [call.kwargs for call in mock_vlguard_download.await_args_list] == [
        {"cache": False},
        {"cache": True},
    ]


@pytest.mark.usefixtures("mock_vlguard_download")
@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("missing_image", "image reference does not resolve"),
        ("missing_filename", "missing image filename"),
        ("missing_safe_flag", "missing or invalid safe flag"),
        ("missing_instruction", "missing or ambiguous"),
        ("empty_instruction", "empty or invalid"),
        ("malformed_instructions", "invalid instr-resp"),
    ],
)
async def test_vlguard_rejects_partially_dropped_records_async(
    *,
    vlguard_variant: tuple[str, str],
    vlguard_records: list[dict[str, Any]],
    image_dir: Path,
    fault: str,
    message: str,
) -> None:
    case_id, instruction_key = vlguard_variant
    row = vlguard_records[1]
    if fault == "missing_image":
        await asyncio.to_thread((image_dir / row["image"]).unlink)
    elif fault == "missing_filename":
        del row["image"]
    elif fault == "missing_safe_flag":
        del row["safe"]
    elif fault == "malformed_instructions":
        row["instr-resp"] = {}
    else:
        item = next(item for item in row["instr-resp"] if instruction_key in item)
        if fault == "missing_instruction":
            del item[instruction_key]
        else:
            item[instruction_key] = ""

    with pytest.raises(AssertionError, match=message):
        await dataset_tests.TestAllDatasets().test_fetch_non_default_variant_async(case_id=case_id)


@pytest.mark.usefixtures("mock_vlguard_download")
async def test_vlguard_rejects_wrong_instruction_field_async(vlguard_variant: tuple[str, str]) -> None:
    case_id, instruction_key = vlguard_variant
    wrong_key = "unsafe_instruction" if instruction_key == "safe_instruction" else "safe_instruction"

    def wrong_instruction(instr_resp: list[dict[str, str]]) -> str:
        return next(item[wrong_key] for item in instr_resp if wrong_key in item)

    with patch.object(dataset_tests._VLGuardDataset, "_extract_instruction", side_effect=wrong_instruction):
        with pytest.raises(AssertionError, match="pairs do not match the raw records"):
            await dataset_tests.TestAllDatasets().test_fetch_non_default_variant_async(case_id=case_id)


@pytest.mark.usefixtures("mock_vlguard_download")
@pytest.mark.parametrize("subcategory", ["disinformation", "other", "unknown-upstream-category"])
async def test_vlguard_harm_category_drift_async(
    *,
    vlguard_variant: tuple[str, str],
    vlguard_records: list[dict[str, Any]],
    subcategory: str,
) -> None:
    case_id, _ = vlguard_variant
    vlguard_records[1]["harmful_subcategory"] = subcategory
    if subcategory == "unknown-upstream-category":
        with pytest.raises(AssertionError, match="unexpectedly fell back to OTHER"):
            await dataset_tests.TestAllDatasets().test_fetch_non_default_variant_async(case_id=case_id)
    else:
        await dataset_tests.TestAllDatasets().test_fetch_non_default_variant_async(case_id=case_id)


@pytest.mark.usefixtures("mock_vlguard_download")
@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("dropped_pair", "pairs do not match the raw records"),
        ("duplicate_seed", "exactly one text/image pair"),
        ("wrong_sequence", "share sequence 0"),
        ("swapped_images", "pairs do not match the raw records"),
        ("lost_subcategory", "pairs do not match the raw records"),
    ],
)
async def test_vlguard_rejects_incorrect_pairs_async(
    *,
    vlguard_variant: tuple[str, str],
    vlguard_records: list[dict[str, Any]],
    fault: str,
    message: str,
) -> None:
    case_id, _ = vlguard_variant
    if fault == "lost_subcategory":
        vlguard_records[0]["harmful_subcategory"] = "disinformation"
    provider = dataset_tests._VARIANT_CASES[case_id].factory()
    dataset = await provider.fetch_dataset_async(cache=False)
    if fault == "dropped_pair":
        dataset.seeds = dataset.seeds[:2]
    elif fault == "duplicate_seed":
        dataset.seeds.append(dataset.seeds[0])
    elif fault == "wrong_sequence":
        dataset.seeds[0].sequence = 1
    elif fault == "lost_subcategory":
        for seed in dataset.seeds[:2]:
            seed.metadata["harmful_subcategory"] = ""
            seed.harm_categories = []
    else:
        dataset.seeds[1].value, dataset.seeds[3].value = dataset.seeds[3].value, dataset.seeds[1].value

    with patch.object(dataset_tests, "_fetch_with_retry_async", new=AsyncMock(return_value=dataset)):
        with pytest.raises(AssertionError, match=message):
            await dataset_tests.TestAllDatasets().test_fetch_non_default_variant_async(case_id=case_id)
