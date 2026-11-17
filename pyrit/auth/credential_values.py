# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Check credential values before a target sends them.

A value pasted with a trailing newline or another control character can't be sent in an HTTP header,
and the HTTP library's error quotes it, so such errors would carry the secret into logs and saved
results. These checks strip surrounding whitespace and refuse what is left over. Errors name the
parameter or header, never its value.
"""

import re
from collections.abc import Mapping

# RFC 9110 token characters, which every header name is made of.
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

#: Lower-case names of headers that control how a request is framed or routed rather than who sends it.
FRAMING_HEADER_NAMES: frozenset[str] = frozenset(
    {"connection", "content-length", "host", "keep-alive", "proxy-connection", "te", "transfer-encoding", "upgrade"}
)


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def clean_credential_value(value: str, *, name: str) -> str:
    """
    Strip surrounding whitespace from a credential and refuse one that can't be sent.

    Args:
        value (str): The credential, such as an API key or a header value.
        name (str): What the value is for, such as ``"api_key"`` or a header name, for errors.

    Returns:
        str: The credential without surrounding whitespace.

    Raises:
        ValueError: If the credential is blank or contains a control character, such as a line break.
    """
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"'{name}' needs a nonblank value.")
    if _has_control_character(cleaned):
        raise ValueError(f"'{name}' contains a line break or another control character.")
    return cleaned


def clean_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """
    Check header names, and strip surrounding whitespace from values and refuse control characters in them.

    An empty value is allowed, since HTTP allows it.

    Args:
        headers (Mapping[str, str]): Header names and values.

    Returns:
        dict[str, str]: The headers with surrounding whitespace removed from names and values.

    Raises:
        ValueError: If a name isn't a valid header name, or a value contains a control character.
    """
    cleaned: dict[str, str] = {}
    for name, value in headers.items():
        stripped = name.strip()
        if not _HEADER_NAME.fullmatch(stripped):
            raise ValueError("A request header name may contain only letters, digits, and !#$%&'*+-.^_`|~.")
        value = value.strip()
        if _has_control_character(value):
            raise ValueError(f"Header '{stripped}' contains a line break or another control character.")
        cleaned[stripped] = value
    return cleaned
