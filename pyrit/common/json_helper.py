# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import IO, Any, Dict, List, cast


def read_json(file: IO[Any]) -> List[Dict[str, str]]:
    """
    Read a JSON file and return its content.

    Returns:
        List[Dict[str, str]]: Parsed JSON content.
    """
    return cast(List[Dict[str, str]], json.load(file))


def write_json(file: IO[Any], examples: List[Dict[str, str]]) -> None:
    """
    Write a list of dictionaries to a JSON file.

    Args:
        file: A file-like object opened for writing JSON data.
        examples (List[Dict[str, str]]): List of dictionaries to write as JSON.
    """
    json.dump(examples, file)


def read_jsonl(file: IO[Any]) -> List[Dict[str, str]]:
    """
    Read a JSONL file and return its content.

    Returns:
        List[Dict[str, str]]: Parsed JSONL content.
    """
    return list(json.loads(line) for line in file)


def write_jsonl(file: IO[Any], examples: List[Dict[str, str]]) -> None:
    """
    Write a list of dictionaries to a JSONL file.

    Args:
        file: A file-like object opened for writing JSONL data.
        examples (List[Dict[str, str]]): List of dictionaries to write as JSONL.
    """
    for example in examples:
        file.write(json.dumps(example) + "\n")
