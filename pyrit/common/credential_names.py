# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Recognize the names that label a credential, such as a settings field, a header, or a query parameter."""

import re
from typing import ClassVar


class CredentialNames:
    """
    Recognize the names that label a credential: a settings field, an HTTP header, or a URL query parameter.

    Some names always label one, such as ``api_key``, ``Authorization``, or a name ending in
    ``_secret``. Others, such as ``key``, ``code``, and ``sig``, label one only where a
    credential is expected: in a URL query, or in a map of query parameters, headers, or
    authentication settings such as ``params``, ``headers``, or ``authentication``. Names
    are compared by their letters and digits alone, in any letter case, as header names
    are matched, so ``X-Api-Key``, ``x_api_key``, ``xapikey``, and ``XaPiKeY`` are one name.
    A switch value such as ``true`` or ``0`` is never a credential, whatever its name.
    """

    NAMES: ClassVar[frozenset[str]] = frozenset(
        {
            "access_key",
            "access_token",
            "account_key",
            "api_key",
            "api_token",
            "auth",
            "auth_token",
            "authentication",
            "authentication_token",
            "authorization",
            "authorization_token",
            "azure_ad_token",
            "azure_speech_key",
            "bearer_token",
            "client_assertion",
            "client_secret",
            "connection_string",
            "cookie",
            "cp4d_token",
            "credential",
            "credentials",
            "ephemeral_key",
            "github_token",
            "hf_access_token",
            "ibm_cloud_token",
            "id_token",
            "id_token_hint",
            "jwt",
            "master_key",
            "oci_key",
            "ocp_apim_subscription_key",
            "passwd",
            "password",
            "private_key",
            "private_token",
            "proxy_authorization",
            "refresh_token",
            "sas_token",
            "secret",
            "secret_key",
            "session_token",
            "set_cookie",
            "shared_access_signature",
            "subscription_key",
            "token",
            "vault_token",
            "watsonx_token",
            "x_amz_credential",
            "x_amz_security_token",
            "x_amz_signature",
            "x_api_key",
            "x_auth_token",
            "x_functions_key",
            "x_goog_credential",
            "x_goog_signature",
            "x_ms_authorization_auxiliary",
            "x_vault_token",
        }
    )
    SUFFIXES: ClassVar[tuple[str, ...]] = (
        "_access_key",
        "_access_key_id",
        "_access_token",
        "_api_key",
        "_api_token",
        "_auth_token",
        "_bearer_token",
        "_connection_string",
        "_credential",
        "_credentials",
        "_password",
        "_private_key",
        "_refresh_token",
        "_sas_token",
        "_secret",
        "_secret_key",
        "_security_token",
        "_session_token",
    )
    EXPECTED_NAMES: ClassVar[frozenset[str]] = frozenset({"code", "key", "sig", "signature"})
    MAP_NAMES: ClassVar[frozenset[str]] = frozenset(
        {
            "auth",
            "authentication",
            "credential",
            "credentials",
            "default_headers",
            "default_query",
            "headers",
            "params",
            "query",
            "query_params",
        }
    )

    FLAG_VALUES: ClassVar[frozenset[str]] = frozenset({"0", "1", "false", "no", "off", "on", "true", "yes"})

    _FOLDED_NAMES: ClassVar[frozenset[str]] = frozenset(name.replace("_", "") for name in NAMES)
    _FOLDED_SUFFIXES: ClassVar[tuple[str, ...]] = tuple(suffix.replace("_", "") for suffix in SUFFIXES)
    _FOLDED_EXPECTED_NAMES: ClassVar[frozenset[str]] = frozenset(name.replace("_", "") for name in EXPECTED_NAMES)
    _FOLDED_MAP_NAMES: ClassVar[frozenset[str]] = frozenset(name.replace("_", "") for name in MAP_NAMES)

    @classmethod
    def is_credential(cls, name: str, *, where_expected: bool = False) -> bool:
        """
        Return whether a name labels a credential.

        Args:
            name (str): The field, header, or query parameter name.
            where_expected (bool): Whether the name sits where a credential is expected, such as
                a URL query, so names such as ``key``, ``code``, and ``sig`` count too.

        Returns:
            bool: Whether the folded name is a credential name or ends like one.
        """
        folded = cls.fold(name)
        if where_expected and folded in cls._FOLDED_EXPECTED_NAMES:
            return True
        return folded in cls._FOLDED_NAMES or folded.endswith(cls._FOLDED_SUFFIXES)

    @classmethod
    def is_credential_map(cls, name: str) -> bool:
        """
        Return whether a setting of this name holds query parameters, headers, or authentication settings.

        Returns:
            bool: Whether the folded name is a known map, such as ``params``, ``headers``, or ``authentication``.
        """
        return cls.fold(name) in cls._FOLDED_MAP_NAMES

    @classmethod
    def is_flag(cls, value: str) -> bool:
        """
        Return whether a value is a switch, such as ``true``, ``off``, or ``0``, which is never a credential.

        Returns:
            bool: Whether the value is a yes/no or 0/1 flag.
        """
        return value.strip().lower() in cls.FLAG_VALUES

    @staticmethod
    def fold(name: str) -> str:
        """
        Fold a name for comparison.

        Returns:
            str: The name's letters and digits in lower case, so ``apiKey``, ``API-KEY``,
            ``api key``, and ``aPi_kEy`` all become ``apikey``.
        """
        return re.sub(r"[^0-9a-z]", "", name.lower())
