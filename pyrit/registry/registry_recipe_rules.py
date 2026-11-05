# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""What a saved registry entry may hold, checked before a saved entry is built."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, unquote, urlsplit

from pyrit.models.catalog.registry_recipe import RestoreFailureReason

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyrit.models.parameter import Parameter

_URL_QUERY_KEYS = frozenset({"api-version"})
_MAX_URL_DECODES = 3


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
    server's settings or identity, extra headers the server adds, or a credential inside a URL. A saved
    target authenticates with Microsoft Entra ID (``auth_mode`` ``"identity"``) or needs no key.

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
    _check_urls(params)


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
        SavedEntryRefusedError: If a parameter the class reads from the server is left empty in an auth
            mode that reads it, or holds a value other than the one that turns the server's value off.
    """
    auth_mode = params.get("auth_mode") or "api_key"
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


def _check_urls(params: Mapping[str, Any]) -> None:
    """
    Refuse a URL that can carry a credential.

    Raises:
        SavedEntryRefusedError: If a value holds a SAS token anywhere, or a URL with a user name,
            password, fragment, or query other than ``api-version``.
    """
    for name, value in params.items():
        texts = value if isinstance(value, list) else [value]
        if any(isinstance(text, str) and _can_carry_credential(text) for text in texts):
            message = (
                f"'{name}' can carry a credential in a URL, which saved entries can't hold. A saved URL has "
                "no user name, password, SAS token, or fragment, and no query except 'api-version'."
            )
            raise SavedEntryRefusedError(reason=RestoreFailureReason.UNSUPPORTED_CREDENTIAL, message=message)


def _can_carry_credential(text: str) -> bool:
    """
    Return whether text holds a SAS token, or is a URL that can carry a credential.

    Percent-encoded and look-alike characters are read too, since an encoded or mistyped SAS token is
    still usable. A URL that can't be parsed counts as one that can carry a credential.

    Returns:
        bool: True when the text holds a SAS token or a URL with user info, a fragment, or another query.
    """
    for form in _readings(text):
        if "sig=" in form.casefold():
            return True
        try:
            parts = urlsplit(form.strip())
            if not parts.netloc:
                continue
            if parts.username is not None or parts.password is not None or parts.fragment:
                return True
        except ValueError:
            return True
        if any(key.casefold() not in _URL_QUERY_KEYS for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
            return True
    return False


def _readings(text: str) -> list[str]:
    """
    List the text as written, percent-decoded, and with look-alike characters replaced.

    Returns:
        list[str]: Each distinct reading of the text.
    """
    decoded = [text]
    while len(decoded) <= _MAX_URL_DECODES and unquote(decoded[-1]) != decoded[-1]:
        decoded.append(unquote(decoded[-1]))
    normalized = [unicodedata.normalize("NFKC", form) for form in decoded]
    return list(dict.fromkeys(decoded + normalized))
