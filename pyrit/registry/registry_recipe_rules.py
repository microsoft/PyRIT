# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""What a saved registry entry may hold, checked before a saved entry is built."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from pyrit.models.catalog import RestoreFailureReason

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.models import Parameter

_URL_SCHEMES = frozenset({"http", "https", "ws", "wss"})
_URL_QUERY_KEYS = frozenset({"api-version"})
_QUERY_SEPARATORS = re.compile(r"[&;]")
_QUERY_VALUE = re.compile(r"[A-Za-z0-9._-]*")
_PATH_PARAMETERS = re.compile(r"[;&=]")
_MAX_URL_READINGS = 8


class SavedEntryRefusedError(ValueError):
    """A recipe holds, or would make its entry use, a credential, or builds a class that can't be saved."""

    def __init__(self, *, reason: RestoreFailureReason, message: str) -> None:
        """
        Initialize the error with the reason to report.

        Args:
            reason (RestoreFailureReason): Why the recipe is refused.
            message (str): Why, in words safe to show users. It never includes a parameter's value.
        """
        super().__init__(message)
        self.reason = reason


def check_saved_entry_params(*, cls: type, parameters: Sequence[Parameter], params: Mapping[str, Any]) -> None:
    """
    Refuse a recipe that a saved entry of ``cls`` can't hold.

    A saved entry is rebuilt from its recipe at every startup, so the recipe can't hold a credential
    and can't leave the entry to use one from this server: an empty key the class would read from the
    server's settings or identity, extra headers the server adds, a destination the server's settings
    choose, or a credential inside a URL. A saved target authenticates with Microsoft Entra ID
    (``auth_mode`` ``"identity"``) or needs no key.

    Args:
        cls (type): The class the recipe builds.
        parameters (Sequence[Parameter]): The class's constructor parameters.
        params (Mapping[str, Any]): The recipe's parameter values.

    Raises:
        SavedEntryRefusedError: If the class can't be saved, or the values hold or would use a credential.
    """
    _check_class(cls)
    _check_stored_credentials(parameters=parameters, params=params)
    _check_server_fallbacks(cls=cls, params=params)
    _check_destinations(cls=cls, params=params)


def check_saveable_class(cls: type) -> tuple[str, ...]:
    """
    Return the auth modes a saved entry of ``cls`` can use, before any of its values are known.

    Runs the class check, then the server-fallback check once per auth mode the class supports, with each
    fallback set to the value that turns it off and every credential left empty. That's the best a saved entry
    of the class can do; the values it's given are checked when it's saved.

    Returns:
        tuple[str, ...]: The supported auth modes that let a saved entry avoid this server's credentials.

    Raises:
        SavedEntryRefusedError: If the class can't be saved, or every auth mode it supports would leave it
            reading a credential from this server.
    """
    _check_class(cls)
    fallbacks: Mapping[str, str | None] = getattr(cls, "server_fallback_parameters", {})
    turned_off = {name: value for name, value in fallbacks.items() if value is not None}
    supported_auth_modes: tuple[str, ...] = tuple(getattr(cls, "supported_auth_modes", None) or ("api_key",))
    saveable_modes: list[str] = []
    for auth_mode in supported_auth_modes:
        try:
            _check_server_fallbacks(cls=cls, params={**turned_off, "auth_mode": auth_mode})
        except SavedEntryRefusedError:
            continue
        saveable_modes.append(auth_mode)
    if not saveable_modes:
        # Only a fallback that no value turns off can refuse every mode, so one exists here.
        needed = [name for name, value in fallbacks.items() if value is None][0]
        raise SavedEntryRefusedError(
            reason=RestoreFailureReason.UNSUPPORTED_CREDENTIAL,
            message=f"'{cls.__name__}' needs a credential in '{needed}', which saved entries can't hold or take "
            "from this server yet.",
        )
    return tuple(saveable_modes)


def _check_class(cls: type) -> None:
    """
    Refuse a class that a saved entry can't build.

    Raises:
        SavedEntryRefusedError: If the class needs the server's upload folder or declares why it can't be saved.
    """
    if getattr(cls, "upload_directory_parameter", None) is not None:
        message = f"'{cls.__name__}' uploads files from a folder the server chooses, which restore can't set yet."
        raise SavedEntryRefusedError(reason=RestoreFailureReason.UNSUPPORTED_CLASS, message=message)
    reason = getattr(cls, "not_saveable_reason", None)
    if reason is not None:
        raise SavedEntryRefusedError(
            reason=RestoreFailureReason.UNSUPPORTED_CLASS, message=f"'{cls.__name__}' can't be saved. {reason}"
        )


def _check_stored_credentials(*, parameters: Sequence[Parameter], params: Mapping[str, Any]) -> None:
    """
    Refuse a value for a parameter the class declares as a credential.

    Raises:
        SavedEntryRefusedError: If a declared credential parameter has a value.
    """
    stored = sorted(
        parameter.name
        for parameter in parameters
        if parameter.sensitive and params.get(parameter.name) not in (None, "")
    )
    if stored:
        raise SavedEntryRefusedError(
            reason=RestoreFailureReason.UNSUPPORTED_CREDENTIAL,
            message=f"Saved entries can't hold credentials ('{stored[0]}').",
        )


def _check_server_fallbacks(*, cls: type, params: Mapping[str, Any]) -> None:
    """
    Refuse values that would let the entry use a credential from this server.

    Raises:
        SavedEntryRefusedError: If the class doesn't support the auth mode, or a parameter the class reads
            from the server is left empty in an auth mode that reads it, or holds a value other than the
            one that turns the server's value off.
    """
    auth_mode = params.get("auth_mode")
    if auth_mode is None:
        auth_mode = "api_key"
    # A class that declares no auth modes takes none, as check_saveable_class assumes.
    supported_auth_modes = getattr(cls, "supported_auth_modes", None) or ("api_key",)
    if auth_mode not in supported_auth_modes:
        modes = ", ".join(f"'{mode}'" for mode in supported_auth_modes)
        raise SavedEntryRefusedError(
            reason=RestoreFailureReason.UNSUPPORTED_CREDENTIAL,
            message=f"'{cls.__name__}' only supports auth_mode {modes}.",
        )
    fallbacks: Mapping[str, str | None] = getattr(cls, "server_fallback_parameters", {})
    for name, turned_off in fallbacks.items():
        value = params.get(name)
        if value in (None, ""):
            if auth_mode == "none" or (auth_mode == "identity" and turned_off is None):
                continue
            message = f"'{name}' is empty, so '{cls.__name__}' can use this server's credential instead."
            if turned_off is not None:
                message += f" Saved entries set it to '{turned_off}'."
            raise SavedEntryRefusedError(reason=RestoreFailureReason.UNSUPPORTED_CREDENTIAL, message=message)
        if value != turned_off:
            raise SavedEntryRefusedError(
                reason=RestoreFailureReason.UNSUPPORTED_CREDENTIAL,
                message=f"'{name}' can carry a credential, which saved entries can't hold.",
            )


def _check_destinations(*, cls: type, params: Mapping[str, Any]) -> None:
    """
    Refuse a destination that this server's settings would choose, or one that can carry a credential.

    Raises:
        SavedEntryRefusedError: If a destination that would take this server's setting is empty or isn't an
            http(s) or ws(s) address, or a destination can carry a credential.
    """
    destinations: Mapping[str, bool] = getattr(cls, "destination_parameters", {})
    for name, takes_server_setting in destinations.items():
        value = params.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            if takes_server_setting:
                message = (
                    f"'{name}' is empty, so '{cls.__name__}' would send requests where this server's settings "
                    "say, with any credential in them. A saved target sets it."
                )
                raise SavedEntryRefusedError(reason=RestoreFailureReason.UNSUPPORTED_CREDENTIAL, message=message)
            continue
        if takes_server_setting and not (isinstance(value, str) and _is_web_address(value)):
            raise SavedEntryRefusedError(
                reason=RestoreFailureReason.INVALID_RECIPE,
                message=f"'{name}' must be a full http(s) or ws(s) address in a saved target.",
            )
        if not isinstance(value, str) or _can_carry_credential(value, address=takes_server_setting):
            message = (
                f"'{name}' can carry a credential in a URL, which saved entries can't hold: a user name, "
                "password, SAS token, fragment, ';', '&' or '=' in the path, or a query other than 'api-version'."
            )
            raise SavedEntryRefusedError(reason=RestoreFailureReason.UNSUPPORTED_CREDENTIAL, message=message)


def _is_web_address(text: str) -> bool:
    """
    Return whether text, as written, is an http(s) or ws(s) address with a host.

    Returns:
        bool: True for an address such as ``https://contoso.openai.azure.com/openai/v1``.
    """
    try:
        parts = urlsplit(text.strip())
    except ValueError:
        return False
    return parts.scheme in _URL_SCHEMES and bool(parts.netloc)


def _can_carry_credential(text: str, *, address: bool) -> bool:
    """
    Return whether a URL or path can carry a credential, in any reading of it.

    A value whose percent-encoding or look-alike characters don't settle within a few readings counts as
    one that can, since an encoded token is still usable.

    Args:
        text (str): The URL or path.
        address (bool): Whether it must be a full http(s) or ws(s) address rather than a path.

    Returns:
        bool: True when a reading isn't a plain address or path, or holds a SAS token, user info, a
            fragment, a path parameter, or a query other than ``api-version``.
    """
    readings = _readings(text)
    return readings is None or any(_reading_can_carry_credential(reading, address=address) for reading in readings)


def _reading_can_carry_credential(text: str, *, address: bool) -> bool:
    """
    Return whether one reading of a URL or path can carry a credential.

    An address has an http(s) or ws(s) scheme and a host without user info. A path has neither, and no
    query. Neither may have a fragment or ';', '&' or '=' in its path, and an address's query may hold
    only ``api-version``.

    Args:
        text (str): One reading of the URL or path.
        address (bool): Whether it must be a full address rather than a path.

    Returns:
        bool: True when the reading isn't a plain address or path, or can carry a credential.
    """
    if "sig=" in text.casefold():
        return True
    try:
        parts = urlsplit(text.strip())
    except ValueError:
        return True
    if address or parts.scheme or parts.netloc:
        if parts.scheme not in _URL_SCHEMES or not parts.netloc or "@" in parts.netloc:
            return True
    elif parts.query:
        return True
    if parts.fragment or _PATH_PARAMETERS.search(parts.path):
        return True
    return not all(_is_version_query(segment) for segment in _QUERY_SEPARATORS.split(parts.query) if segment)


def _is_version_query(segment: str) -> bool:
    """
    Return whether one query segment is ``api-version`` with a version-shaped value.

    Returns:
        bool: True for ``api-version=2024-10-21`` and similar.
    """
    key, _, value = segment.partition("=")
    return key.strip().casefold() in _URL_QUERY_KEYS and _QUERY_VALUE.fullmatch(value.strip()) is not None


def _readings(text: str) -> list[str] | None:
    """
    List the text as written and as each round of look-alike replacement and percent-decoding reads it.

    Returns:
        list[str] | None: The readings until they stop changing, or None if they still change after the limit.
    """
    readings = [text]
    while len(readings) <= _MAX_URL_READINGS:
        reading = unquote(unicodedata.normalize("NFKC", readings[-1]))
        if reading == readings[-1]:
            return readings
        readings.append(reading)
    return None
