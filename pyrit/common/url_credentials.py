# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Recognize and hide the credentials a URL can carry."""

import re
from collections.abc import Iterator
from typing import ClassVar
from urllib.parse import ParseResult, unquote, unquote_plus, urlparse

from pyrit.common.credential_names import CredentialNames


class UrlCredentials:
    """
    Recognize and hide the credentials a URL can carry.

    A URL carries a credential in its user information (``https://user:password@host``
    or a token as the user name) or in a query or fragment parameter whose name labels
    one (``CredentialNames``), such as an Azure SAS ``sig``, an Azure Functions ``code``,
    or an ``api-key``.
    """

    MASK: ClassVar[str] = "***"
    # The "://" after a scheme, where an authority starts.
    _AUTHORITY_START_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"(?<=[A-Za-z0-9+.-])://")
    # Where a URL written inside another starts: a scheme, which begins with a letter.
    _NESTED_URL_START_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*://")
    # What ends an authority, as a URL parser reads it.
    _AUTHORITY_END_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[/?#\s]")
    # What ends user information that holds an unencoded "/", "?", or "#": a space or another URL.
    _UNENCODED_AUTHORITY_END_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\s|://")
    # A host, in brackets when it is an IPv6 address, and a port of digits.
    _HOST_AND_PORT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"(?:\[[^\]]*\]|[^:\[\]]*)(?::\d*)?")
    # A "name=value" parameter after "?", "#", "&", or ";"; quoting and closing punctuation end it.
    _PARAMETER_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"(?<=[?#&;])([^=?&#;\s'\"<>()\[\]{},]+)=([^&#;\s'\"<>()\[\]{},]*)"
    )

    @classmethod
    def has_credentials(cls, url: str) -> bool:
        """
        Return whether a string is a URL that carries a credential.

        Args:
            url (str): The string to check; strings that are not URLs carry none.

        Returns:
            bool: Whether the URL has user information or a credential query or fragment parameter.
        """
        return next(cls.secrets(url), None) is not None

    @classmethod
    def mask(cls, url: str) -> str:
        """
        Replace the credentials a URL carries with ``***``, keeping the rest of it.

        User information is read as ``user_information`` reads it, including that of a path that
        starts with ``//``, such as a request target joined onto a host. After a credential
        parameter, the fragment up to its first ``&`` is taken as the rest of the credential,
        written with an unencoded ``#``. A URL its parser cannot read is masked as ``mask_text``
        masks it.

        Args:
            url (str): The URL to mask.

        Returns:
            str: The URL with its user information and credential parameter values masked.
        """
        parsed = cls._parse(url)
        if parsed is None:
            return cls.mask_text(url)
        _, at, host = parsed.netloc.rpartition("@")
        path = parsed.path
        path_user_information = cls._path_user_information(path)
        if path_user_information is not None:
            path = f"//{cls.MASK}{path[2 + len(path_user_information) :]}"
        query = cls._mask_parameters(parsed.query)
        fragment = parsed.fragment
        tail, separator, rest = fragment.partition("&")
        if tail and query.endswith(f"={cls.MASK}"):
            fragment = f"{cls.MASK}{separator}{rest}"
        return cls.mask_text(
            parsed._replace(
                netloc=f"{cls.MASK}@{host}" if at else host,
                path=path,
                query=query,
                fragment=cls._mask_parameters(fragment),
            ).geturl()
        )

    @classmethod
    def mask_text(cls, text: str) -> str:
        """
        Replace the credentials of the URLs in a text with ``***``, reading them as written.

        The user information after each ``://``, read as ``user_information`` reads it, and the
        value of any credential parameter after a ``?``, ``#``, ``&``, or ``;`` are masked without
        parsing the URL, so a URL that a parser rejects, such as one an error message quotes
        because it is malformed, is masked too. A ``//`` inside a path starts no user information.

        Args:
            text (str): The text to mask, such as an error message.

        Returns:
            str: The text with that user information and those parameter values replaced by ``***``.
        """
        pieces: list[str] = []
        position = 0
        for match in cls._AUTHORITY_START_PATTERN.finditer(text):
            at = cls._user_information_end(text, start=match.end())
            if at >= 0:
                pieces += [text[position : match.end()], cls.MASK]
                position = at
        pieces.append(text[position:])
        return cls._PARAMETER_PATTERN.sub(
            lambda match: (
                f"{match.group(1)}={cls.MASK}"
                if cls._is_credential_parameter(name=match.group(1), value=match.group(2))
                else match.group(0)
            ),
            "".join(pieces),
        )

    @classmethod
    def user_information(cls, text: str) -> str | None:
        """
        Read the user information at the start of a URL's authority, as written.

        As a URL parser reads it, an authority ends at the first ``/``, ``?``, or ``#``, and its
        user information at its last ``@``. When what follows that user information (all of the
        authority when it holds none) cannot be a host and a port, such as ``user:abc`` in
        ``user:abc/def@host`` or ``example.com:abc`` in ``alice@example.com:abc/def@host``, a
        password held a character a URL must percent-encode, so the user information runs to the
        last ``@`` before a space or another ``://``. A token with no password, or a password whose
        first such character follows only digits, must be percent-encoded to be read whole.

        Args:
            text (str): What follows the ``//`` before an authority.

        Returns:
            str | None: The user information, or ``None`` when the authority holds none.
        """
        at = cls._user_information_end(text, start=0)
        return text[:at] if at >= 0 else None

    @classmethod
    def secrets(cls, url: str) -> Iterator[str]:
        """
        Yield the credentials a URL carries, both as written and decoded.

        Args:
            url (str): The URL to read; other strings carry none.

        Yields:
            str: Each user name, password, and credential parameter value, including those of a
            path that starts with ``//`` and of a URL written inside this one, as written or
            percent-encoded, such as a proxy's upstream URL and its key in a query parameter.
        """
        parsed = cls._parse(url)
        if parsed is None:
            return
        parts = [parsed.username, parsed.password]
        for user_information in (
            cls.user_information(url.partition("//")[2]),
            cls._path_user_information(parsed.path),
            *cls._nested_user_information(parsed),
        ):
            user, _, password = (user_information or "").partition(":")
            parts += [user, password]
        for part in parts:
            if part:
                yield part
                yield unquote(part)
        for value in (*cls._credential_values(parsed), *cls._nested_parameter_credentials(parsed)):
            yield value
            yield unquote_plus(value)

    @classmethod
    def query_credentials(cls, query: str) -> Iterator[str]:
        """
        Yield the values of the credential parameters in a query string, as written.

        Args:
            query (str): A query string such as ``api-version=1&code=abc``, without the ``?``.

        Yields:
            str: Each credential parameter value that is neither empty nor a flag such as ``true``.
        """
        for name, _, value in (part.partition("=") for part in query.split("&")):
            if cls._is_credential_parameter(name=name, value=value):
                yield value

    @classmethod
    def _nested_user_information(cls, parsed: ParseResult) -> Iterator[str]:
        """
        Read the user information of each URL written inside a URL, as written and percent-decoded.

        One written in the path is read through the rest of the URL, as a URL parser reads its
        password, which can hold ";" and, unencoded, "?" or "#". One written in a query or fragment
        parameter is read within it, so it never runs into the next, such as an email in a sibling.

        Yields:
            str: The user information of each URL written inside the parsed one.
        """
        path = parsed.path + (f";{parsed.params}" if parsed.params else "")
        rest = (f"?{parsed.query}" if parsed.query else "") + (f"#{parsed.fragment}" if parsed.fragment else "")
        parameters = (*parsed.query.split("&"), *parsed.fragment.split("&"))
        for start, end in ((path, rest), *((parameter, "") for parameter in parameters)):
            for head, tail in dict.fromkeys(((start, end), (unquote(start), unquote(end)))):
                text = head + tail
                for match in cls._NESTED_URL_START_PATTERN.finditer(head):
                    at = cls._user_information_end(text, start=match.end())
                    if at >= 0:
                        yield text[match.end() : at]

    @classmethod
    def _nested_parameter_credentials(cls, parsed: ParseResult) -> Iterator[str]:
        """
        Read the credential parameters of a URL written inside one query or fragment parameter.

        Such a parameter can hold another URL with its own query, such as a proxy's upstream URL
        with a key, so each one is read on its own, as written and decoded, from the scheme of the
        URL inside it; ordinary text such as ``q=error?code=404`` holds no URL and is not read.

        Yields:
            str: Each credential parameter value that is neither empty nor a flag.
        """
        for text in (*parsed.query.split("&"), *parsed.fragment.split("&")):
            for candidate in dict.fromkeys((text, unquote(text))):
                nested = cls._NESTED_URL_START_PATTERN.search(candidate)
                if nested is None:
                    continue
                for match in cls._PARAMETER_PATTERN.finditer(candidate, nested.start()):
                    value = candidate[match.start(2) : match.end(2)]
                    if cls._is_credential_parameter(name=match.group(1), value=value):
                        yield value

    @classmethod
    def _path_user_information(cls, path: str) -> str | None:
        """
        Read the user information of a path that starts with ``//``, as a request target joined onto a host has.

        Returns:
            str | None: The user information, or ``None`` when the path holds none.
        """
        return cls.user_information(path[2:]) if path.startswith("//") else None

    @classmethod
    def _user_information_end(cls, text: str, *, start: int) -> int:
        """
        Find the ``@`` that ends the user information of the authority at ``start``, as ``user_information`` reads it.

        Returns:
            int: The index of that ``@``, or -1 when the authority holds no user information.
        """
        found = cls._AUTHORITY_END_PATTERN.search(text, start)
        end = found.start() if found else len(text)
        at = text.rfind("@", start, end)
        if not cls._HOST_AND_PORT_PATTERN.fullmatch(text, max(at + 1, start), end):
            found = cls._UNENCODED_AUTHORITY_END_PATTERN.search(text, start)
            end = found.start() if found else len(text)
            at = text.rfind("@", start, end)
        return at

    @classmethod
    def _mask_parameters(cls, text: str) -> str:
        """
        Mask the values of credential parameters in a query or fragment.

        Returns:
            str: The query or fragment with each credential value replaced by ``***``.
        """
        return "&".join(cls._mask_parameter(part) for part in text.split("&"))

    @classmethod
    def _mask_parameter(cls, part: str) -> str:
        """
        Mask the value of one ``name=value`` parameter if it is a credential.

        Returns:
            str: The parameter, with its value replaced by ``***`` when it is a credential.
        """
        name, _, value = part.partition("=")
        if cls._is_credential_parameter(name=name, value=value):
            return f"{name}={cls.MASK}"
        return part

    @staticmethod
    def _is_credential_parameter(*, name: str, value: str) -> bool:
        """
        Return whether a ``name=value`` parameter, as written, carries a credential.

        Returns:
            bool: Whether the name labels a credential and the value is neither empty nor a flag.
        """
        return (
            bool(value)
            and CredentialNames.is_credential(unquote_plus(name), where_expected=True)
            and not CredentialNames.is_flag(unquote_plus(value))
        )

    @classmethod
    def _credential_values(cls, parsed: ParseResult) -> Iterator[str]:
        """
        Yield the values of credential parameters in a URL's query and fragment, as written.

        Yields:
            str: Each credential parameter value that is neither empty nor a flag.
        """
        yield from cls.query_credentials(parsed.query)
        yield from cls.query_credentials(parsed.fragment)

    @staticmethod
    def _parse(url: str) -> ParseResult | None:
        """
        Parse a string that is a URL with a scheme and a host.

        Returns:
            ParseResult | None: The parsed URL, or ``None`` for any other string.
        """
        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        return parsed if parsed.scheme and parsed.netloc else None
