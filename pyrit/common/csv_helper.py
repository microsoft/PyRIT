# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import csv
from typing import IO, Any


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
