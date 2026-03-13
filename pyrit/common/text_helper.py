# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import IO, Any


def read_txt(file: IO[Any]) -> list[dict[str, str]]:
    """
    Read a TXT file and return its content.

    Returns:
        List[Dict[str, str]]: Parsed TXT content.
    """
    return [{"prompt": line.strip()} for line in file.readlines()]


def write_txt(file: IO[Any], examples: list[dict[str, str]]) -> None:
    """
    Write a list of dictionaries to a TXT file.

    Args:
        file: A file-like object opened for writing TXT data.
        examples (List[Dict[str, str]]): List of dictionaries to write as TXT.
    """
    file.write("\n".join([ex["prompt"] for ex in examples]))
