# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import re
from typing import IO, Any, TypeGuard


def is_non_empty_string(value: object) -> TypeGuard[str]:
    """
    Check whether an untrusted value is a string containing non-whitespace text.

    Returns:
        bool: Whether the value is a non-empty string.
    """
    return isinstance(value, str) and bool(value.strip())


# C0 control characters except tab and newline, DEL, and the C1 range. C1 includes the
# single-character CSI (U+009B) and OSC (U+009D) introducers that some terminals honor.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def read_txt(file: IO[Any]) -> list[dict[str, str]]:
    """
    Read a TXT file and return its content.

    Returns:
        list[dict[str, str]]: Parsed TXT content.
    """
    return [{"prompt": line.strip()} for line in file.readlines() if line.strip()]


def write_txt(file: IO[Any], examples: list[dict[str, str]]) -> None:
    """
    Write a list of dictionaries to a TXT file.

    Args:
        file: A file-like object opened for writing TXT data.
        examples (list[dict[str, str]]): List of dictionaries to write as TXT.
    """
    file.write("\n".join([ex["prompt"] for ex in examples]))


def escape_control_characters(text: str) -> str:
    """
    Replace terminal control characters with their visible escaped form.

    Target-controlled text can carry ANSI/OSC sequences (cursor movement, OSC 8
    hyperlinks, OSC 52 clipboard writes). Console printers pass text through this
    function so the operator's terminal displays the sequences instead of acting on
    them. Tab and newline are kept, and all other text, including non-ASCII
    characters, is returned unchanged.

    Unicode format characters are deliberately out of scope, so the bidirectional
    controls that ``BidiConverter`` emits still reach the terminal. Escaping the whole
    category would also mangle legitimate text such as emoji ZWJ sequences.

    Args:
        text (str): The text to escape.

    Returns:
        str: The text with every control character replaced by its escaped form, e.g. ``\\x1b``.
    """
    return _CONTROL_CHARACTERS.sub(lambda match: repr(match.group())[1:-1], text)
