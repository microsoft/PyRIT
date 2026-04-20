# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import IO, Any, Dict, List


def read_txt(file: IO[Any]) -> List[Dict[str, str]]:
    """
    Read a TXT file and return its content.

    Returns:
        List[Dict[str, str]]: Parsed TXT content.
    """
    return [{"prompt": line.strip()} for line in file.readlines()]


def write_txt(file: IO[Any], examples: List[Dict[str, str]]) -> None:
    """
    Write a list of dictionaries to a TXT file.

    Args:
        file: A file-like object opened for writing TXT data.
        examples (List[Dict[str, str]]): List of dictionaries to write as TXT.
    """
    file.write("\n".join([ex["prompt"] for ex in examples]))
