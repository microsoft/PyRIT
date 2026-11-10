# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Lightweight Azure Storage URI helpers."""

import re
from urllib.parse import parse_qs, urlparse

# A scheme followed by "://" and everything up to whitespace, so a quote can't end a URL before its query.
_URL_IN_TEXT = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://\S+")
# The signature of a SAS token, which can also appear in text without a scheme.
_SAS_SIGNATURE = re.compile(r"(?i)\bsig=[^&\s]+")


def is_azure_blob_uri(value: str, *, min_path_segments: int = 1) -> bool:
    """
    Return whether a value is a trusted Azure Blob Storage HTTPS URI.

    Args:
        value (str): URI to validate.
        min_path_segments (int): Minimum number of non-empty path segments. Defaults to 1.

    Returns:
        bool: True when the URI uses a known Azure Blob authority and has the required path.
    """
    parsed_uri = urlparse(value)
    hostname = parsed_uri.hostname or ""
    azure_blob_host_suffixes = (
        ".blob.core.windows.net",
        ".blob.core.chinacloudapi.cn",
        ".blob.core.usgovcloudapi.net",
        ".blob.core.cloudapi.de",
    )
    path_segments = [segment for segment in parsed_uri.path.split("/") if segment]
    return (
        parsed_uri.scheme == "https"
        and any(hostname.endswith(suffix) and hostname != suffix[1:] for suffix in azure_blob_host_suffixes)
        and parsed_uri.username is None
        and parsed_uri.password is None
        and parsed_uri.port is None
        and len(path_segments) >= min_path_segments
    )


def has_sas_signature(value: str) -> bool:
    """
    Return whether a URI contains an Azure SAS signature parameter.

    Returns:
        bool: True when the query contains a non-empty ``sig`` parameter.
    """
    return bool(parse_qs(urlparse(value).query).get("sig"))


def redact_url_credentials(value: str) -> str:
    """
    Remove the user name and password, query parameters, and fragment from a URL for display or logging.

    Returns:
        str: The URL without them.
    """
    parsed = urlparse(value)
    return parsed._replace(netloc=parsed.netloc.rpartition("@")[2], query="", fragment="").geturl()


def redact_urls(text: str) -> str:
    """
    Remove the user name and password, query parameters, and fragment from every URL in a text.

    A query or user name can hold a credential, such as a SAS token. A URL that can't be parsed is
    hidden entirely, and a SAS signature is hidden wherever it appears.

    Returns:
        str: The text with each URL redacted.
    """

    def redact(match: re.Match[str]) -> str:
        try:
            return redact_url_credentials(match.group(0))
        except ValueError:
            return "<URL hidden>"

    return _SAS_SIGNATURE.sub("sig=<hidden>", _URL_IN_TEXT.sub(redact, text))
