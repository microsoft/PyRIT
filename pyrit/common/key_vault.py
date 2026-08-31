# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared Azure Key Vault helpers."""

import urllib.parse

_KEY_VAULT_DNS_SUFFIXES = frozenset({"vault.azure.net", "vault.azure.cn", "vault.usgovcloudapi.net"})


def parse_key_vault_secret_uri(secret_uri: str) -> tuple[str, str, str | None]:
    """
    Parse a Key Vault secret URI into its vault URL, secret name, and optional version.

    Args:
        secret_uri: Full secret URI in the format
            ``https://{vault}.{vault-dns-suffix}/secrets/{name}[/{version}]``.

    Returns:
        A tuple containing the vault URL, secret name, and optional secret version.

    Raises:
        ValueError: If the URI is not a valid Azure Key Vault secret URI.
    """
    error_message = (
        f"Invalid Azure Key Vault secret URI: '{secret_uri}'. Expected an HTTPS Azure Key Vault URI in the format "
        "https://{vault}.{vault-dns-suffix}/secrets/{name}[/{version}]."
    )
    try:
        parsed_uri = urllib.parse.urlsplit(secret_uri)
        port = parsed_uri.port
    except (TypeError, ValueError) as error:
        raise ValueError(error_message) from error

    hostname = parsed_uri.hostname
    vault_name, separator, dns_suffix = hostname.partition(".") if hostname else ("", "", "")
    valid_vault_name = 1 <= len(vault_name) <= 63 and all(
        char.isascii() and (char.isalnum() or char == "-") for char in vault_name
    )
    valid_authority = (
        parsed_uri.scheme.casefold() == "https"
        and parsed_uri.username is None
        and parsed_uri.password is None
        and port is None
        and separator == "."
        and dns_suffix in _KEY_VAULT_DNS_SUFFIXES
        and valid_vault_name
    )
    path_parts = parsed_uri.path.split("/")
    valid_path = (
        len(path_parts) in {3, 4} and path_parts[0] == "" and path_parts[1] == "secrets" and all(path_parts[2:])
    )
    if not valid_authority or not valid_path or parsed_uri.query or parsed_uri.fragment:
        raise ValueError(error_message)

    secret_name, secret_version = path_parts[2], path_parts[3] if len(path_parts) == 4 else None
    identifiers = [secret_name] + ([secret_version] if secret_version else [])
    if any(
        not 1 <= len(identifier) <= 127
        or not all(char.isascii() and (char.isalnum() or char == "-") for char in identifier)
        for identifier in identifiers
    ):
        raise ValueError(error_message)

    return f"https://{hostname}", secret_name, secret_version
