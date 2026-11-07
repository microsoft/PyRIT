# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import csv
import io
from typing import IO, Any


def strip_leading_comment_block(text: str) -> str:
    """
    Drop the leading "#" comment and blank lines of CSV text.

    Mirrors the scan in ``HumanLabeledDataset._read_leading_comment_block``: only the
    leading block is a comment, so a "#" that starts a data row or sits inside a cell
    is preserved as data. Callers that hand the result to ``csv.DictReader`` must use
    this instead of dropping every "#" line, which silently deletes data.

    Universal newline recognition keeps the block boundary identical to the canonical
    scan without splitting on separators such as "\\v". Newline characters are retained,
    including CR and CRLF inside quoted cells. CSV readers must also preserve newlines.

    Args:
        text (str): The full CSV text.

    Returns:
        str: The text from the first non-comment, non-blank line onward.
    """
    stream = io.StringIO(text, newline="")
    remaining: list[str] = []
    for line in stream:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            remaining.append(line)
            break
    remaining.append(stream.read())
    return "".join(remaining)


def read_csv(file: IO[Any]) -> list[dict[str, str]]:
    """
    Read a CSV file and return its rows as dictionaries.

    Returns:
        list[dict[str, str]]: Parsed CSV rows as dictionaries.
    """
    reader = csv.DictReader(file)
    return list(reader)


def write_csv(file: IO[Any], examples: list[dict[str, str]]) -> None:
    """
    Write a list of dictionaries to a CSV file.

    The header is the union of the keys across all rows, in first-seen order, so a
    row carrying a key the first row lacks is written instead of failing halfway
    through the file. Rows missing a header key get an empty cell.

    Args:
        file: A file-like object opened for writing CSV data.
        examples (list[dict[str, str]]): List of dictionaries to write as CSV rows.
    """
    if not examples:
        return

    fieldnames: list[str] = []
    seen: set[str] = set()
    for example in examples:
        for key in example:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    writer = csv.DictWriter(file, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(examples)
