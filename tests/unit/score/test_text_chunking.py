# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.score.text_chunking import iter_chunk_spans


@pytest.mark.parametrize(
    ("length", "size", "overlap", "expected"),
    [
        (0, 5, 0, [(0, 0)]),
        (5, 5, 0, [(0, 5)]),
        (6, 5, 0, [(0, 5), (5, 6)]),
        (10, 5, 0, [(0, 5), (5, 10)]),
        (9, 5, 1, [(0, 5), (4, 9)]),
        (10, 5, 1, [(0, 5), (4, 9), (8, 10)]),
        (7, 5, 4, [(0, 5), (1, 6), (2, 7)]),
    ],
)
def test_chunk_spans_cover_sequence(length: int, size: int, overlap: int, expected: list[tuple[int, int]]) -> None:
    spans = list(iter_chunk_spans(length=length, chunk_length=size, overlap=overlap))
    assert spans == expected
    assert {index for start, end in spans for index in range(start, end)} == set(range(length))
    assert all(end - start <= size for start, end in spans)


@pytest.mark.parametrize(("length", "size", "overlap"), [(-1, 5, 0), (1, 0, 0), (1, 5, -1), (1, 5, 5)])
def test_chunk_spans_reject_invalid_lengths(length: int, size: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="Require"):
        list(iter_chunk_spans(length=length, chunk_length=size, overlap=overlap))
