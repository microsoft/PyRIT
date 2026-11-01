# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the remote Garak leak replay cloze corpora."""

import io
from pathlib import Path
from unittest.mock import patch

import pytest

from pyrit.datasets.seed_datasets.remote import __all__ as remote_exports
from pyrit.datasets.seed_datasets.remote.garak_leak_replay_dataset import (
    _GarakLeakReplayDataset,
    _GarakLeakReplayGuardianDataset,
    _GarakLeakReplayLiteratureDataset,
    _GarakLeakReplayNYTDataset,
    _GarakLeakReplayPotterDataset,
    _read_cloze_tsv,
    _write_cloze_tsv,
)
from pyrit.models import SeedOrigin, SeedPrompt

COMMIT = "3f50ea5ff9cd7050099940647c15c39b07a93392"
QUOTED_TSV = (
    'Avery\t"Wait," said [MASK], and the lamps went out.\n'
    "Brook\tNobody answered when [MASK] knocked.\n"
    'Corin\tThe sign read "Closed", so [MASK] went home.\n'
)


@pytest.mark.parametrize(
    ("loader_class", "dataset_name", "source_file", "corpus"),
    [
        (_GarakLeakReplayLiteratureDataset, "garak_leak_replay_literature", "book_cloze.tsv", "literature"),
        (_GarakLeakReplayPotterDataset, "garak_leak_replay_potter", "potter_cloze.tsv", "potter"),
        (_GarakLeakReplayNYTDataset, "garak_leak_replay_nyt", "nyt_cloze.tsv", "nyt"),
        (_GarakLeakReplayGuardianDataset, "garak_leak_replay_guardian", "guardian_cloze.tsv", "guardian"),
    ],
)
def test_each_corpus_has_a_pinned_garak_url(
    loader_class: type[_GarakLeakReplayDataset], dataset_name: str, source_file: str, corpus: str
) -> None:
    loader = loader_class()

    assert loader.dataset_name == dataset_name
    assert corpus == loader.CORPUS
    assert loader.source_type == "public_url"
    assert loader.source == f"https://raw.githubusercontent.com/NVIDIA/garak/{COMMIT}/garak/data/{source_file}"
    assert loader_class.__name__ in remote_exports


def test_lines_with_quotation_marks_stay_separate_rows() -> None:
    rows = _read_cloze_tsv(io.StringIO(QUOTED_TSV))

    assert rows == [
        {"trigger": "Avery", "passage": '"Wait," said [MASK], and the lamps went out.'},
        {"trigger": "Brook", "passage": "Nobody answered when [MASK] knocked."},
        {"trigger": "Corin", "passage": 'The sign read "Closed", so [MASK] went home.'},
    ]


@pytest.mark.parametrize("line", ["Avery only\n", "Avery\tone [MASK]\textra\n", "\n"])
def test_line_without_two_fields_raises(line: str) -> None:
    with pytest.raises(ValueError, match="line 2 has"):
        _read_cloze_tsv(io.StringIO("Brook\tNobody answered when [MASK] knocked.\n" + line))


def test_cache_format_round_trips() -> None:
    rows = _read_cloze_tsv(io.StringIO(QUOTED_TSV))
    buffer = io.StringIO()

    _write_cloze_tsv(buffer, rows)
    buffer.seek(0)

    assert buffer.getvalue() == QUOTED_TSV
    assert _read_cloze_tsv(buffer) == rows


async def test_fetch_keeps_each_passage_verbatim_with_its_trigger() -> None:
    loader = _GarakLeakReplayNYTDataset()
    rows = _read_cloze_tsv(io.StringIO(QUOTED_TSV))

    with patch.object(loader, "_fetch_from_url", return_value=rows) as fetch:
        dataset = await loader.fetch_dataset_async(cache=False)

    assert fetch.call_args.kwargs == {"source": loader.source, "source_type": "public_url", "cache": False}
    assert dataset.dataset_name == "garak_leak_replay_nyt"
    assert [seed.value for seed in dataset.seeds] == [row["passage"] for row in rows]
    assert [seed.metadata for seed in dataset.seeds] == [
        {"trigger": "Avery", "corpus": "nyt", "line": 1},
        {"trigger": "Brook", "corpus": "nyt", "line": 2},
        {"trigger": "Corin", "corpus": "nyt", "line": 3},
    ]
    for seed in dataset.seeds:
        assert isinstance(seed, SeedPrompt)
        assert seed.data_type == "text"
        assert seed.origin is SeedOrigin.REMOTE
        assert seed.source == _GarakLeakReplayNYTDataset.dataset_url()
        assert seed.harm_categories == ["copyright", "training_data_leakage"]


async def test_fetch_from_local_file_parses_without_quote_handling(tmp_path: Path) -> None:
    source = tmp_path / "cloze.tsv"
    source.write_text(QUOTED_TSV, encoding="utf-8")
    loader = _GarakLeakReplayLiteratureDataset(source=str(source), source_type="file")

    dataset = await loader.fetch_dataset_async(cache=False)

    assert [seed.metadata["trigger"] for seed in dataset.seeds] == ["Avery", "Brook", "Corin"]
    assert dataset.seeds[2].value == 'The sign read "Closed", so [MASK] went home.'


@pytest.mark.parametrize(
    ("row", "match"),
    [
        ({"trigger": " ", "passage": "Nobody answered when [MASK] knocked."}, "empty trigger"),
        ({"trigger": "Brook", "passage": "Nobody answered."}, "exactly one"),
        ({"trigger": "Brook", "passage": "[MASK] and [MASK] knocked."}, "exactly one"),
    ],
)
async def test_malformed_rows_raise(row: dict[str, str], match: str) -> None:
    loader = _GarakLeakReplayGuardianDataset()

    with patch.object(loader, "_fetch_from_url", return_value=[row]):
        with pytest.raises(ValueError, match=match):
            await loader.fetch_dataset_async()


async def test_empty_corpus_raises() -> None:
    loader = _GarakLeakReplayPotterDataset()

    with patch.object(loader, "_fetch_from_url", return_value=[]):
        with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
            await loader.fetch_dataset_async()
