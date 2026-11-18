# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Check credential values before a target sends them.

The HTTP client expects ASCII and rejects control characters in header values. Validate before
sending so that transport errors cannot quote malformed credentials. Errors name the parameter
or header, never its value.
"""

import re
from collections.abc import Mapping

# RFC 9110 token characters, which every header name is made of.
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

#: Lower-case names of headers that control how a request is framed or routed rather than who sends it.
FRAMING_HEADER_NAMES: frozenset[str] = frozenset(
    {"connection", "content-length", "host", "keep-alive", "proxy-connection", "te", "transfer-encoding", "upgrade"}
)


def _has_control_character(value: str, *, allow_tab: bool = False) -> bool:
    return any(
        (ord(character) < 0x20 and not (allow_tab and character == "\t")) or ord(character) == 0x7F
        for character in value
    )


def clean_credential_value(value: str, *, name: str) -> str:
    """
    Strip surrounding whitespace from a credential and refuse one that can't be sent.

    Args:
        value (str): The credential, such as an API key or a header value.
        name (str): What the value is for, such as ``"api_key"`` or a header name, for errors.

    Returns:
        str: The credential without surrounding whitespace.

    Raises:
        ValueError: If the credential is blank, contains non-ASCII text, or contains a control character.
    """
    if not value.isascii():
        raise ValueError(f"'{name}' must contain only ASCII characters.")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"'{name}' needs a nonblank value.")
    if _has_control_character(cleaned):
        raise ValueError(f"'{name}' contains a line break or another control character.")
    return cleaned


def clean_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """
    Check header names and values, trimming surrounding whitespace.

    An empty value and a tab inside a value are allowed, since HTTP allows them.

    Args:
        headers (Mapping[str, str]): Header names and values.

    Returns:
        dict[str, str]: The headers with surrounding whitespace removed from names and values.

    Raises:
        ValueError: If a name isn't a valid header name, or a value contains non-ASCII text or a control character.
    """
    cleaned: dict[str, str] = {}
    for name, value in headers.items():
        stripped = name.strip()
        if not _HEADER_NAME.fullmatch(stripped):
            raise ValueError("A request header name may contain only letters, digits, and !#$%&'*+-.^_`|~.")
        if not value.isascii():
            raise ValueError(f"Header '{stripped}' must contain only ASCII characters.")
        value = value.strip()
        if _has_control_character(value, allow_tab=True):
            raise ValueError(f"Header '{stripped}' contains a line break or another control character.")
        cleaned[stripped] = value
    return cleaned
