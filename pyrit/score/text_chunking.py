# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared windows over character or token sequences used by scorers."""

from collections.abc import Iterator


def iter_chunk_spans(*, length: int, chunk_length: int, overlap: int = 0) -> Iterator[tuple[int, int]]:
    """
    Yield half-open spans that cover a sequence, including a single span for empty input.

    Args:
        length (int): Sequence length, in characters or tokens.
        chunk_length (int): Maximum number of items per window.
        overlap (int): Number of items shared by adjacent windows.

    Yields:
        tuple[int, int]: Start and end offsets in the caller's units.

    Raises:
        ValueError: If lengths are invalid or overlap prevents forward progress.
    """
    if length < 0 or chunk_length <= 0 or not 0 <= overlap < chunk_length:
        raise ValueError("Require length >= 0, chunk_length > 0, and 0 <= overlap < chunk_length.")
    start = 0
    while True:
        end = min(start + chunk_length, length)
        yield start, end
        if end == length:
            return
        start = end - overlap
